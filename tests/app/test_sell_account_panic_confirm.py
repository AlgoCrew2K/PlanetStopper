"""
RED tests — MED-6: /api/sell_account typed-confirmation + LIVE_EXECUTION gate + Discord audit.

All tests in this module MUST fail against the current app.py until the implementer adds:
  - confirm_account_id field validation (typo-protection)
  - confirm_phrase == "LIQUIDATE" validation (second factor)
  - Discord webhook call on every invocation (live + paper)
  - alphabot_daemon.log ERROR-level entry on every invocation (via logging module)

Fixtures: tests/fixtures/app/panic_confirm/*.json
Mock boundary: Composer go-to-cash + Discord webhook — never real network calls.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "app" / "panic_confirm"


def _load(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _live_env(live: bool = True) -> dict:
    """Return a minimal .env dict for sell_account tests."""
    return {
        "LIVE_EXECUTION": "True" if live else "False",
        "COMPOSER_KEY_ID": "test-key-id",
        "COMPOSER_SECRET": "test-secret",
        "DISCORD_WEBHOOK_URL": "https://discord.test/webhook",
    }


# ---------------------------------------------------------------------------
# AC-1: valid full payload + LIVE_EXECUTION=True → liquidation executes
# ---------------------------------------------------------------------------


def test_sell_account_valid_live_executes_liquidation(client, monkeypatch):
    """
    POST with all three required fields AND LIVE_EXECUTION=True must:
      - return 200
      - include 'executed': true in the body
      - NOT return dry_run: true
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))

    started = []

    class CapturingThread:
        def __init__(self, target=None, args=(), **kw):
            started.append(args)

        def start(self):
            pass  # do not execute real network calls

    monkeypatch.setattr(app_module.threading, "Thread", CapturingThread)

    # Mock Discord so it does not call real webhook
    monkeypatch.setattr(app_module.requests, "post", MagicMock())

    payload = _load("valid_live_request.json")
    resp = client.post("/api/sell_account", json=payload)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("executed") is True, "live execution must set executed=True"
    assert body.get("dry_run") is not True, "live execution must NOT set dry_run=True"


# ---------------------------------------------------------------------------
# AC-2: valid full payload + LIVE_EXECUTION=False → dry-run, no Composer call
# ---------------------------------------------------------------------------


def test_sell_account_valid_paper_returns_dry_run_no_composer_call(client, monkeypatch):
    """
    LIVE_EXECUTION=False + valid body → 200 with dry_run=True, executed=False.
    NO Composer go-to-cash POST must be issued.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(False))

    composer_posts = []

    def fake_post(url, **_kw):
        if "go-to-cash" in url:
            composer_posts.append(url)
        m = MagicMock()
        m.status_code = 200
        return m

    monkeypatch.setattr(app_module.requests, "post", fake_post)

    payload = _load("valid_live_request.json")
    resp = client.post("/api/sell_account", json=payload)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("dry_run") is True, "paper mode must set dry_run=True"
    assert body.get("executed") is False, "paper mode must set executed=False"
    assert composer_posts == [], "go-to-cash must NOT be called in paper mode"


# ---------------------------------------------------------------------------
# AC-3: missing confirm_account_id → 400
# ---------------------------------------------------------------------------


def test_sell_account_missing_confirm_account_id_returns_400(client, monkeypatch):
    """POST without confirm_account_id must return 400 with a reason string."""
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    payload = _load("missing_confirm_account_id.json")
    resp = client.post("/api/sell_account", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert "confirm_account_id" in (body.get("message") or body.get("reason") or ""), (
        "400 body must name the missing field for operator clarity"
    )


# ---------------------------------------------------------------------------
# AC-4: confirm_account_id does not match account_id → 400
# ---------------------------------------------------------------------------


def test_sell_account_mismatched_confirm_account_id_returns_400(client, monkeypatch):
    """Typo in confirm_account_id (does not match account_id) must return 400."""
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    payload = _load("mismatched_confirm_account_id.json")
    assert payload["account_id"] != payload["confirm_account_id"], (
        "fixture sanity: fields must differ so the endpoint can detect the mismatch"
    )
    resp = client.post("/api/sell_account", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    reason = body.get("message") or body.get("reason") or ""
    assert reason, "400 must include a reason message"


# ---------------------------------------------------------------------------
# AC-5: missing confirm_phrase → 400
# ---------------------------------------------------------------------------


def test_sell_account_missing_confirm_phrase_returns_400(client, monkeypatch):
    """POST without confirm_phrase must return 400."""
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    payload = _load("missing_confirm_phrase.json")
    resp = client.post("/api/sell_account", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert "confirm_phrase" in (body.get("message") or body.get("reason") or ""), (
        "400 body must name the missing field"
    )


# ---------------------------------------------------------------------------
# AC-6: confirm_phrase is not exactly "LIQUIDATE" → 400
# ---------------------------------------------------------------------------


def test_sell_account_wrong_confirm_phrase_returns_400(client, monkeypatch):
    """
    confirm_phrase must be the exact string "LIQUIDATE" (case-sensitive).
    "liquidate", "LIQUIDATE ", "Liquidate" etc. must all return 400.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    payload = _load("wrong_confirm_phrase.json")
    assert payload["confirm_phrase"] != "LIQUIDATE", "fixture sanity: phrase must not be LIQUIDATE"
    resp = client.post("/api/sell_account", json=payload)
    assert resp.status_code == 400


def test_sell_account_confirm_phrase_case_sensitive_reject_uppercase_variant(client, monkeypatch):
    """Extra guard: 'Liquidate' (mixed case) must also return 400."""
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    resp = client.post(
        "/api/sell_account",
        json={
            "account_id": "ACC1",
            "confirm_account_id": "ACC1",
            "confirm_phrase": "Liquidate",
        },
    )
    assert resp.status_code == 400


def test_sell_account_confirm_phrase_case_sensitive_reject_with_trailing_space(client, monkeypatch):
    """'LIQUIDATE ' (trailing space) must return 400 — exact match required."""
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    resp = client.post(
        "/api/sell_account",
        json={
            "account_id": "ACC1",
            "confirm_account_id": "ACC1",
            "confirm_phrase": "LIQUIDATE ",
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# AC-7: Discord webhook called on every invocation (live AND paper)
# ---------------------------------------------------------------------------


def test_sell_account_discord_webhook_called_on_live_invocation(client, monkeypatch):
    """
    Every call to /api/sell_account — even with LIVE_EXECUTION=True — must
    trigger a Discord webhook post with a payload that contains the account ID
    and a timestamp marker.  This is the audit trail requirement.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))

    discord_calls = []

    def fake_post(url, **kwargs):
        if "discord" in url or "webhook" in url:
            discord_calls.append({"url": url, "kwargs": kwargs})
        m = MagicMock()
        m.status_code = 200
        return m

    monkeypatch.setattr(app_module.requests, "post", fake_post)

    class NoOpThread:
        def __init__(self, **_kw):
            pass

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", NoOpThread)

    payload = _load("valid_live_request.json")
    resp = client.post("/api/sell_account", json=payload)

    assert resp.status_code == 200
    assert discord_calls, "Discord webhook must be called on live invocation"

    # The payload content must include the account ID and 'EMERGENCY LIQUIDATION'
    webhook_body = discord_calls[0]["kwargs"]
    raw_json = str(webhook_body)
    assert payload["account_id"] in raw_json, (
        "Discord payload must name the account being liquidated"
    )
    assert "EMERGENCY LIQUIDATION" in raw_json or "LIQUIDAT" in raw_json, (
        "Discord payload must contain emergency liquidation marker text"
    )


def test_sell_account_discord_webhook_called_on_paper_invocation(client, monkeypatch):
    """
    Discord audit must fire even in paper mode (LIVE_EXECUTION=False).
    Operator needs an audit trail regardless of mode.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(False))

    discord_calls = []

    def fake_post(url, **kwargs):
        if "discord" in url or "webhook" in url:
            discord_calls.append(url)
        m = MagicMock()
        m.status_code = 200
        return m

    monkeypatch.setattr(app_module.requests, "post", fake_post)

    payload = _load("valid_live_request.json")
    resp = client.post("/api/sell_account", json=payload)

    assert resp.status_code == 200
    assert discord_calls, "Discord webhook must be called even in paper mode"


# ---------------------------------------------------------------------------
# AC-8: alphabot_daemon.log ERROR-level entry via logging module
# ---------------------------------------------------------------------------


def test_sell_account_logs_error_level_entry_on_live_invocation(client, monkeypatch):
    """
    Every /api/sell_account call must emit an ERROR-level log entry via the
    Python logging module (not just a print or file.write).  The logger name
    used by the module is asserted to be 'alphabot' or the module-level logger.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    monkeypatch.setattr(app_module.requests, "post", MagicMock())

    class NoOpThread:
        def __init__(self, **_kw):
            pass

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", NoOpThread)

    log_records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = CapturingHandler()
    # Attach to root so we catch any logger name the implementer chooses.
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    original_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)

    try:
        payload = _load("valid_live_request.json")
        resp = client.post("/api/sell_account", json=payload)
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(original_level)

    assert resp.status_code == 200
    error_records = [r for r in log_records if r.levelno >= logging.ERROR]
    assert error_records, (
        "alphabot_daemon.log must receive an ERROR-level entry via the logging module on every invocation"
    )
    # The log message must contain the account ID — so the operator can correlate
    combined = " ".join(r.getMessage() for r in error_records)
    assert payload["account_id"] in combined, (
        "ERROR log entry must include the account ID for correlation"
    )


def test_sell_account_logs_error_level_entry_on_paper_invocation(client, monkeypatch):
    """ERROR log entry is required for paper-mode invocations too."""
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(False))
    monkeypatch.setattr(app_module.requests, "post", MagicMock())

    log_records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = CapturingHandler()
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    original_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)

    try:
        payload = _load("valid_live_request.json")
        resp = client.post("/api/sell_account", json=payload)
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(original_level)

    assert resp.status_code == 200
    error_records = [r for r in log_records if r.levelno >= logging.ERROR]
    assert error_records, "ERROR log must fire even in paper mode"


# ---------------------------------------------------------------------------
# AC-9/10: UI — template presence tests
# ---------------------------------------------------------------------------


def test_template_contains_emergency_liquidate_button(tmp_path):
    """
    The index.html template must contain an 'Emergency Liquidate' button
    (trigger for the new hardened modal).
    """
    template_path = (
        Path(__file__).parent.parent.parent / "templates" / "index.html"
    )
    content = template_path.read_text(encoding="utf-8")
    # The button text or id must surface the emergency liquidation concept.
    assert (
        "Emergency Liquidate" in content
        or "emergency-liquidate" in content
        or "emergency_liquidate" in content
    ), (
        "templates/index.html must contain an Emergency Liquidate button trigger"
    )


def test_template_contains_panic_modal_with_required_elements():
    """
    The panic-confirm modal must include:
      - an account dropdown (select element or equivalent sourced from accounts_map)
      - a text input for the confirm phrase
      - a confirm button (red / rose class)
      - a cancel button
    """
    template_path = (
        Path(__file__).parent.parent.parent / "templates" / "index.html"
    )
    content = template_path.read_text(encoding="utf-8")

    # Account dropdown — must have a select or combobox targeting account IDs
    assert (
        "panic-account" in content
        or "liquidate-account" in content
        or "accounts_map" in content
    ), "modal must have an account selector sourced from accounts_map"

    # Confirm-phrase text input
    assert (
        "panic-phrase" in content
        or "liquidate-phrase" in content
        or "confirm-phrase" in content
        or "confirm_phrase" in content
    ), "modal must contain a confirm-phrase text input"

    # Confirm button with a red/rose visual treatment
    assert "rose" in content or "bg-red" in content, (
        "confirm button must have a red/rose CSS class for operator safety signaling"
    )

    # Cancel button
    assert "Cancel" in content, "modal must contain a Cancel button"


# ---------------------------------------------------------------------------
# AC-11: client-side validation — confirm button disabled until phrase matches
# ---------------------------------------------------------------------------


def test_template_js_disables_confirm_until_liquidate_typed():
    """
    The JS in index.html must enforce that the confirm button stays disabled
    until the text input value equals 'LIQUIDATE'.  We check the template
    source for the pattern — this is a static analysis of the client contract.
    """
    template_path = (
        Path(__file__).parent.parent.parent / "templates" / "index.html"
    )
    content = template_path.read_text(encoding="utf-8")

    # The JS must compare the input value to the exact string "LIQUIDATE"
    assert '"LIQUIDATE"' in content or "'LIQUIDATE'" in content, (
        "JS must compare confirm-phrase input to the exact string LIQUIDATE"
    )

    # The confirm button must be conditioned on .disabled
    assert "disabled" in content, "JS must set .disabled on the confirm button"


# ---------------------------------------------------------------------------
# AC-12: server-side validation survives curl bypass (no client trust)
# ---------------------------------------------------------------------------


def test_sell_account_server_rejects_tampered_payload_missing_confirm_fields(client, monkeypatch):
    """
    Even if the UI is bypassed, a POST missing confirm_account_id and
    confirm_phrase must get 400 — the server does NOT trust the client.
    This is a duplicate of ACs 3/5 from the server perspective, expressed as
    a 'curl attacker' scenario.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))

    # Simulate curl: only account_id, no confirmation fields
    resp = client.post(
        "/api/sell_account",
        json={"account_id": "ACC-LIVE-001"},
        headers={"X-Custom-Client": "curl/7.88"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("status") == "error", "server must return error status, not 500"


# ---------------------------------------------------------------------------
# AC-14: legacy payload (old fields only) → 400, NOT 500
# ---------------------------------------------------------------------------


def test_sell_account_legacy_payload_returns_400_not_500(client, monkeypatch):
    """
    A legacy POST body (only account_id, no new confirmation fields) must
    return 400 — graceful validation error, not a 500 server crash.
    This guards against the endpoint silently breaking old integrations with
    a crash instead of an informative rejection.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _live_env(True))
    payload = _load("legacy_missing_new_fields.json")
    resp = client.post("/api/sell_account", json=payload)

    # Must be 400 (bad request) — never 500 (server error)
    assert resp.status_code == 400, (
        f"legacy payload must produce 400 not {resp.status_code}; "
        "the endpoint must validate gracefully rather than crash"
    )
    body = resp.get_json()
    assert body.get("status") == "error"
