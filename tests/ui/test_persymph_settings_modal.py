"""
RED tests — per-symphony Settings Modal (persymph-fe cycle).

Surfaces covered:
  AC-1   Gear icon on each symphony card AND table row opens the modal
  AC-2   Effective-mode badge = f(global LIVE_EXECUTION, symphony.live_mode)
           Live (both on) / Dry-run (global on, symph off) /
           Dry-run (global off) caution variant
  AC-3   Live toggle defaults to symphony's live_mode; OFF→ON requires
           ConfirmGoLive dialog — bare toggle click NEVER persists live
  AC-4   Global LIVE_EXECUTION OFF → caution banner + pre-armed toggle styling
  AC-5   Live AND global on → danger banner
  AC-6   Locked-vars checklist editable; Save persists symphony_strategies.locked_vars
  AC-7   Autotuner parameters render READ-ONLY (no edit control)
  AC-8   AI-advisor section: display-only, no apply button, empty-state text
  AC-9   Saving live_mode → set_symphony_live_mode → config_audit_log row
  AC-10  POST /api/settings: live_mode + locked_vars allowlisted; non-allowlisted
           keys rejected; CSRF enforced; LIVE_EXECUTION (global) never written
  AC-11  Default dry-run preserved; is_live stays explicit (arch rule 4)
  AC-12  Save-error state renders on save failure

Design source:
  .design-handoff/persymph-modal/claude-design/alphabotpm/project/settings-modal.jsx
Backend already on main (bdb5fd7):
  database.set_symphony_live_mode / get_symphony_live_mode / get_symphony_strategy
  config_audit_log table (migration 030)
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, call, patch

import pytest

import app as app_module
import database

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = (
    __import__("pathlib").Path(__file__).parent.parent.parent / "templates"
)
_STATIC_DIR = (
    __import__("pathlib").Path(__file__).parent.parent.parent / "static"
)

# The 6 autotuner-owned tuning parameters shown read-only in the modal.
_TUNING_PARAMS = [
    "trail_pct",
    "tp_target",
    "vwap_bleed_mult",
    "vol_scale",
    "mc_prob_floor",
    "para_ratchet_acc",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_db_one_symphony(monkeypatch):
    """Patch database so the dashboard has one live-capable symphony."""
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {
            "sym_alpha": {
                "id": "sym_alpha",
                "name": "Alpha Momentum",
                "position": 0.8,
                "current_return": 3.5,
                "mc_prob": 0.65,
            }
        }
        db_mock.normalize_name.side_effect = (
            lambda n: (n or "").lower().replace(" ", "_").replace("-", "_")
        )
        db_mock.get_symphony_strategy.return_value = {
            "params": {k: 1.0 for k in database.DEFAULT_STRATEGY},
            "locked_vars": ["trail_pct"],
            "live_mode": False,
        }
        db_mock.get_advisor_observations_for_symphony.return_value = []
        yield db_mock


@pytest.fixture
def mock_db_live_symphony(monkeypatch):
    """Symphony with live_mode=True for danger-banner tests."""
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {
            "sym_alpha": {
                "id": "sym_alpha",
                "name": "Alpha Momentum",
                "position": 0.8,
                "current_return": 3.5,
                "mc_prob": 0.65,
            }
        }
        db_mock.normalize_name.side_effect = (
            lambda n: (n or "").lower().replace(" ", "_").replace("-", "_")
        )
        db_mock.get_symphony_strategy.return_value = {
            "params": {k: 1.0 for k in database.DEFAULT_STRATEGY},
            "locked_vars": [],
            "live_mode": True,
        }
        db_mock.get_advisor_observations_for_symphony.return_value = []
        yield db_mock


def _mock_env(monkeypatch, live_execution: str = "False", extra=None):
    env = {
        "LIVE_EXECUTION": live_execution,
        "EXECUTION_START_TIME": "09:30",
        "EXIT_AUTHORITY": "per_symphony",
        "COMPOSER_KEY_ID": "ck-test",
        "COMPOSER_SECRET": "cs-test",
        "ACCOUNT_INDIVIDUAL": "ai-test",
        "ACCOUNT_ROTH": "ar-test",
        "ACCOUNT_TRAD": "at-test",
        "ALPACA_KEY": "ak-test",
        "ALPACA_SECRET": "as-test",
        "DISCORD_WEBHOOK_URL": "https://discord.test/webhook",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    if extra:
        env.update(extra)
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: env)
    return env


def _disable_csrf(monkeypatch):
    monkeypatch.setattr(app_module, "_csrf_check_enabled", False)


# ---------------------------------------------------------------------------
# AC-1 — Gear icon anchors
# ---------------------------------------------------------------------------


def test_dashboard_renders_gear_icon_on_symphony_card(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-1: index.html must include a gear icon/button that opens the per-symphony modal.

    The gear icon must be present on the symphony card (or table row) so the
    operator can access per-symphony settings.  A bare data-testid or role
    attribute is acceptable evidence of the gear anchor.
    """
    _mock_env(monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200, f"GET / must return 200; got {resp.status_code}"
    html = resp.get_data(as_text=True)

    # Accept: gear unicode, data-testid containing "gear" or "settings", or
    # aria-label containing "settings" (case-insensitive).
    has_gear = (
        "⚙" in html
        or "gear" in html.lower()
        or "settings-modal" in html
        or "data-sym-settings" in html
        or "open-sym-settings" in html
        or "symph-settings" in html
        or "symphony-settings" in html
    )
    assert has_gear, (
        "AC-1: index.html must render a gear icon or settings-trigger element for each "
        "symphony card.  Expected a gear character (⚙), or a data-testid / aria-label "
        "referencing 'gear' or 'settings'.  The modal anchor is missing."
    )


def test_dashboard_gear_icon_carries_symphony_id(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-1: Each gear button must carry a data attribute referencing the symphony id.

    Without this the JS cannot tell which symphony was opened — a modal that
    shows the wrong symphony's data is a safety hazard for live-mode toggling.
    """
    _mock_env(monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Accept data-symphony-id, data-sym-id, data-sym, data-id on the gear/settings button.
    has_id_attr = (
        "data-symphony-id" in html
        or "data-sym-id" in html
        or "data-symphony=" in html
        or "data-symid" in html
        or "sym_alpha" in html  # the fixture symphony id must appear
    )
    assert has_id_attr, (
        "AC-1: The gear/settings anchor must carry a data attribute with the symphony "
        "id so the JS modal knows which symphony to load.  'sym_alpha' or a "
        "data-symphony-id attribute is missing from the rendered dashboard."
    )


# ---------------------------------------------------------------------------
# AC-2 — /api/symphony-settings GET: live_mode in response
# ---------------------------------------------------------------------------


def test_get_symphony_settings_returns_live_mode_key(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-2: GET /api/symphony-settings/<sym> must return a `live_mode` key.

    The effective-mode badge computation in the JS modal depends on both
    `live_mode` (per-symphony) and `global_live` (from env).  If `live_mode`
    is absent the badge will always show Dry-run even when the symphony is
    armed — an incorrect and potentially dangerous display.
    """
    _mock_env(monkeypatch)
    resp = client.get("/api/symphony-settings/alpha_momentum")
    assert resp.status_code == 200, (
        f"GET /api/symphony-settings/<sym> must return 200; got {resp.status_code}. "
        "Route may not be registered yet."
    )
    body = resp.get_json()
    assert body is not None, "Response body must be JSON"
    assert "live_mode" in body, (
        "AC-2: GET /api/symphony-settings/<sym> must include 'live_mode' key so the "
        "effective-mode badge can evaluate the 3-way truth table."
    )


def test_get_symphony_settings_live_mode_is_bool(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-2: `live_mode` in symphony-settings response must be a boolean (or 0/1 int).

    The JS modal evaluates `live_mode` as a truthy flag; a string "False" would
    be truthy and misrepresent the symphony's effective mode.
    """
    _mock_env(monkeypatch)
    resp = client.get("/api/symphony-settings/alpha_momentum")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "live_mode" in body, "live_mode key must be present for this assertion to run"
    live_mode_val = body["live_mode"]
    assert isinstance(live_mode_val, (bool, int)), (
        f"AC-2: 'live_mode' must be a bool or int (0/1), not {type(live_mode_val).__name__!r}. "
        "A string 'False' is truthy — the effective-mode badge would show LIVE when it should "
        "show Dry-run."
    )


def test_get_symphony_settings_returns_global_live_key(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-2: GET /api/symphony-settings/<sym> must return `global_live` (LIVE_EXECUTION env).

    The 3-way effective-mode computation requires both the per-symphony flag
    AND the global master-switch.  Returning only live_mode forces the JS to
    make a second GET call (or duplicate the env-read), which violates the
    single-endpoint contract expected by the modal.
    """
    _mock_env(monkeypatch, live_execution="False")
    resp = client.get("/api/symphony-settings/alpha_momentum")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "global_live" in body, (
        "AC-2: GET /api/symphony-settings/<sym> must include 'global_live' key "
        "(derived from LIVE_EXECUTION env) so the modal can display the full "
        "3-way effective-mode badge without a second API call."
    )


def test_effective_mode_live_when_both_on(
    client, mock_db_live_symphony, monkeypatch
):
    """AC-2: Effective mode = Live when global_live=True AND live_mode=True."""
    _mock_env(monkeypatch, live_execution="True")
    resp = client.get("/api/symphony-settings/alpha_momentum")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("live_mode") is True or body.get("live_mode") == 1, (
        "AC-2: live_mode must be True for a live-armed symphony"
    )
    assert body.get("global_live") is True or body.get("global_live") == 1, (
        "AC-2: global_live must be True when LIVE_EXECUTION=True"
    )


def test_effective_mode_dry_run_when_global_off(
    client, mock_db_live_symphony, monkeypatch
):
    """AC-2: Effective mode = Dry-run (global off) when LIVE_EXECUTION=False even if live_mode=True."""
    _mock_env(monkeypatch, live_execution="False")
    resp = client.get("/api/symphony-settings/alpha_momentum")
    assert resp.status_code == 200
    body = resp.get_json()
    # global_live must be False/falsy regardless of per-symphony live_mode
    global_live = body.get("global_live")
    assert not global_live, (
        "AC-2: global_live must be False/falsy when LIVE_EXECUTION=False. "
        "The effective-mode badge must show 'Dry-run (global off)' even when live_mode=True."
    )


# ---------------------------------------------------------------------------
# AC-3 — Confirm-to-go-live invariant (backend: bare POST never persists live)
# ---------------------------------------------------------------------------


def test_post_live_mode_true_without_confirm_flag_is_rejected(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-3: POST /api/symphony-settings without confirm=true must NOT set live_mode=True.

    A bare toggle click (no ConfirmGoLive dialog) must be blocked at the API
    layer — the confirm dialog is UX friction but the real gate is server-side.
    This test verifies the implementation cannot be bypassed by JS-skipping the
    dialog and POSTing directly.
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    set_live_calls = []

    def fake_set_live(name, live, operator):
        set_live_calls.append({"name": name, "live": live, "operator": operator})

    with patch.object(app_module.database, "set_symphony_live_mode", side_effect=fake_set_live):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={"live_mode": True},  # no confirm=True
            content_type="application/json",
        )

    # Either the server rejects with 4xx, or it accepts but set_symphony_live_mode
    # was NOT called with live=1 (because confirm was absent).
    if resp.status_code == 200:
        live_set_to_true = any(c["live"] == 1 or c["live"] is True for c in set_live_calls)
        assert not live_set_to_true, (
            "AC-3: POST /api/symphony-settings with live_mode=True but NO confirm=True "
            "must NOT call set_symphony_live_mode with live=1. "
            "A bare toggle click must never persist live without explicit confirmation."
        )
    else:
        assert resp.status_code in (400, 403, 422), (
            f"AC-3: Expected 400/403/422 when live_mode=True without confirm; "
            f"got {resp.status_code}"
        )


def test_post_live_mode_true_with_confirm_flag_calls_set_live(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-3: POST /api/symphony-settings with confirm=true DOES call set_symphony_live_mode.

    This verifies the happy-path: after the operator clicks 'Yes, go live' in
    the ConfirmGoLive dialog, the POST reaches set_symphony_live_mode(live=1).
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    set_live_calls = []

    def fake_set_live(name, live, operator):
        set_live_calls.append({"name": name, "live": live, "operator": operator})

    with patch.object(app_module.database, "set_symphony_live_mode", side_effect=fake_set_live):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={"live_mode": True, "confirm": True},
            content_type="application/json",
        )

    assert resp.status_code == 200, (
        f"AC-3: POST with live_mode=True + confirm=True must return 200; got {resp.status_code}"
    )
    assert len(set_live_calls) == 1, (
        "AC-3: set_symphony_live_mode must be called exactly once after confirmed live-mode POST"
    )
    assert set_live_calls[0]["live"] in (1, True), (
        "AC-3: set_symphony_live_mode must be called with live=1 after confirmation"
    )


def test_post_live_mode_false_does_not_require_confirm(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-3: Toggling OFF (live_mode=False) is immediate — no confirm dialog required.

    ON→OFF is safe (disables real orders) so it must never require confirmation.
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    set_live_calls = []

    def fake_set_live(name, live, operator):
        set_live_calls.append({"name": name, "live": live, "operator": operator})

    with patch.object(app_module.database, "set_symphony_live_mode", side_effect=fake_set_live):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={"live_mode": False},  # no confirm needed
            content_type="application/json",
        )

    assert resp.status_code == 200, (
        f"AC-3: POST with live_mode=False must return 200 without confirm; got {resp.status_code}"
    )
    assert len(set_live_calls) == 1, (
        "AC-3: set_symphony_live_mode must be called once for OFF toggle (no confirm required)"
    )
    assert set_live_calls[0]["live"] in (0, False), (
        "AC-3: set_symphony_live_mode must be called with live=0 for OFF toggle"
    )


# ---------------------------------------------------------------------------
# AC-9 — config_audit_log row on live_mode change
# ---------------------------------------------------------------------------


def test_live_mode_change_writes_audit_log_row(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-9: POST live_mode change must produce a config_audit_log row via set_symphony_live_mode.

    set_symphony_live_mode already encapsulates the audit write — this test
    verifies the route calls set_symphony_live_mode (not save_symphony_strategy
    which does NOT write audit rows).
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    set_live_calls = []
    save_strategy_calls = []

    def fake_set_live(name, live, operator):
        set_live_calls.append({"name": name, "live": live})

    def fake_save_strategy(name, params, locked):
        save_strategy_calls.append(name)

    with (
        patch.object(app_module.database, "set_symphony_live_mode", side_effect=fake_set_live),
        patch.object(app_module.database, "save_symphony_strategy", side_effect=fake_save_strategy),
    ):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={"live_mode": True, "confirm": True},
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert len(set_live_calls) >= 1, (
        "AC-9: live_mode change must call set_symphony_live_mode (which writes "
        "config_audit_log), not save_symphony_strategy (which does not)."
    )


def test_audit_log_operator_field_populated(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-9: set_symphony_live_mode must be called with a non-empty operator string.

    The audit log is useless if every row has operator=''.  The route must pass
    a meaningful operator identifier (e.g. 'dashboard' or the request IP).
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    set_live_calls = []

    def fake_set_live(name, live, operator):
        set_live_calls.append({"name": name, "live": live, "operator": operator})

    with patch.object(app_module.database, "set_symphony_live_mode", side_effect=fake_set_live):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={"live_mode": False},
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert len(set_live_calls) == 1
    operator = set_live_calls[0]["operator"]
    assert isinstance(operator, str) and len(operator) > 0, (
        "AC-9: set_symphony_live_mode must be called with a non-empty operator string. "
        f"Got: {operator!r}"
    )


# ---------------------------------------------------------------------------
# AC-10 — POST /api/settings allowlist + CSRF + global LIVE_EXECUTION guard
# ---------------------------------------------------------------------------


def test_post_settings_accepts_live_mode_in_symphony_payload(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-10: POST /api/settings must accept live_mode when scoped inside the symphonies dict.

    live_mode is a per-symphony key inside the 'symphonies' dict, not a global
    key — it should never appear in the globals allowlist check.
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    set_live_calls = []

    def fake_set_live(name, live, operator):
        set_live_calls.append({"name": name, "live": live})

    # POST /api/settings with live_mode inside the symphonies sub-payload.
    # This is the EXISTING settings route; per-symphony live_mode may go through
    # a dedicated /api/symphony-settings route instead.  Both must work correctly.
    with patch.object(app_module.database, "set_symphony_live_mode", side_effect=fake_set_live):
        resp = client.post(
            "/api/settings",
            json={
                "globals": {},
                "symphonies": {
                    "alpha_momentum": {
                        "params": {},
                        "locked_vars": [],
                        "live_mode": True,
                        "confirm": True,
                    }
                },
            },
            content_type="application/json",
        )

    # 400 is acceptable ONLY if the route rejects live_mode-in-symphonies (then
    # it must go through /api/symphony-settings instead).  200 is acceptable if
    # the route handles it correctly.
    assert resp.status_code in (200, 400), (
        f"AC-10: POST /api/settings with live_mode in symphonies payload must return "
        f"200 or 400, not {resp.status_code}"
    )


def test_post_settings_rejects_live_execution_in_globals(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-10: POST /api/settings MUST reject LIVE_EXECUTION in the globals payload.

    LIVE_EXECUTION is explicitly excluded from _SETTINGS_WRITE_ALLOWLIST.
    Arming real-money execution via an unauthenticated dashboard POST is
    categorically forbidden.  Any implementation that allows it is a safety
    regression.
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)

    resp = client.post(
        "/api/settings",
        json={"globals": {"LIVE_EXECUTION": "True"}, "symphonies": {}},
        content_type="application/json",
    )
    assert resp.status_code == 400, (
        "AC-10: POST /api/settings with LIVE_EXECUTION in globals must return 400. "
        "LIVE_EXECUTION is intentionally excluded from _SETTINGS_WRITE_ALLOWLIST — "
        "the global master-switch must never be toggled via the dashboard POST."
    )
    body = resp.get_json()
    assert body is not None and body.get("status") == "error", (
        "AC-10: Response body must include status='error' for rejected key"
    )


def test_post_settings_rejects_arbitrary_non_allowlisted_global_key(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-10: POST /api/settings must reject any global key not in _SETTINGS_WRITE_ALLOWLIST."""
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)

    resp = client.post(
        "/api/settings",
        json={"globals": {"MALICIOUS_KEY": "injected"}, "symphonies": {}},
        content_type="application/json",
    )
    assert resp.status_code == 400, (
        "AC-10: POST /api/settings must return 400 for arbitrary non-allowlisted keys. "
        "The allowlist must enforce a closed write surface."
    )


def test_post_settings_csrf_enforced_without_token(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-10: POST /api/settings without a valid X-CSRF-Token must return 403.

    CSRF is enforced by @before_request on all POST routes.  A symphonies modal
    save without a CSRF token must be rejected regardless of payload validity.
    """
    _mock_env(monkeypatch)
    # Do NOT disable CSRF — we're testing that it fires.
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)

    resp = client.post(
        "/api/settings",
        json={"globals": {}, "symphonies": {}},
        content_type="application/json",
        # Deliberately omit X-CSRF-Token header
    )
    assert resp.status_code == 403, (
        f"AC-10: POST /api/settings without X-CSRF-Token must return 403. "
        f"Got {resp.status_code}.  CSRF protection must not be bypassable."
    )


def test_post_symphony_settings_csrf_enforced(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-10: POST /api/symphony-settings without CSRF token must return 403.

    The per-symphony settings route (new route) must also be covered by the
    @before_request CSRF guard.  A new route that accidentally bypasses CSRF
    is a live-mode arming vector.
    """
    _mock_env(monkeypatch)
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)

    resp = client.post(
        "/api/symphony-settings/alpha_momentum",
        json={"live_mode": False},
        content_type="application/json",
    )
    assert resp.status_code == 403, (
        f"AC-10: POST /api/symphony-settings/<sym> without X-CSRF-Token must return 403. "
        f"Got {resp.status_code}.  The new route must be covered by @before_request CSRF."
    )


# ---------------------------------------------------------------------------
# AC-11 — Default dry-run; is_live explicit
# ---------------------------------------------------------------------------


def test_new_symphony_defaults_to_dry_run(
    client, monkeypatch
):
    """AC-11: A symphony with no row in symphony_strategies must default to live_mode=False.

    Arch rule 4: is_live=True is explicit, never by omission.  get_symphony_strategy
    returns live_mode=False when no row exists — this test verifies the
    /api/symphony-settings route reflects that default.
    """
    _mock_env(monkeypatch)
    with patch.object(app_module, "database") as db_mock:
        db_mock.normalize_name.side_effect = (
            lambda n: (n or "").lower().replace(" ", "_").replace("-", "_")
        )
        # Simulate no existing row — function returns the default dict with live_mode=False
        db_mock.get_symphony_strategy.return_value = {
            "params": database.DEFAULT_STRATEGY.copy(),
            "locked_vars": database.DEFAULT_LOCKED_VARS.copy(),
            "live_mode": False,
        }
        db_mock.get_advisor_observations_for_symphony.return_value = []

        resp = client.get("/api/symphony-settings/brand_new_symphony")

    if resp.status_code == 200:
        body = resp.get_json()
        live_mode = body.get("live_mode")
        assert live_mode is False or live_mode == 0, (
            f"AC-11: A new/unknown symphony must default to live_mode=False (dry-run). "
            f"Got live_mode={live_mode!r}.  Arch rule 4: is_live=True is explicit, never "
            f"by omission."
        )


def test_settings_write_allowlist_excludes_live_execution():
    """AC-11: _SETTINGS_WRITE_ALLOWLIST must NOT contain LIVE_EXECUTION.

    This is a direct inspection of the module-level constant — verifies the
    arch rule is encoded in the allowlist itself and not just checked at
    runtime.
    """
    assert "LIVE_EXECUTION" not in app_module._SETTINGS_WRITE_ALLOWLIST, (
        "AC-11: LIVE_EXECUTION must be excluded from _SETTINGS_WRITE_ALLOWLIST. "
        "This constant is the enforcement boundary for arch rule 4 — removing "
        "it would silently allow dashboard POSTs to arm real money."
    )


# ---------------------------------------------------------------------------
# AC-6 — Locked-vars checklist persisted via POST /api/symphony-settings
# ---------------------------------------------------------------------------


def test_post_symphony_settings_persists_locked_vars(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-6: POST /api/symphony-settings must persist locked_vars via save_symphony_strategy.

    The locked-vars checklist changes (not live_mode) go through save_symphony_strategy.
    live_mode changes go through set_symphony_live_mode.  This test covers the
    locked_vars path specifically.
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    save_calls = []

    def fake_save(name, params, locked):
        save_calls.append({"name": name, "params": params, "locked": locked})

    with patch.object(app_module.database, "save_symphony_strategy", side_effect=fake_save):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={
                "locked_vars": ["trail_pct", "vol_scale"],
                "live_mode": False,
            },
            content_type="application/json",
        )

    assert resp.status_code == 200, (
        f"AC-6: POST /api/symphony-settings must return 200 for locked_vars update; "
        f"got {resp.status_code}"
    )
    assert len(save_calls) >= 1, (
        "AC-6: save_symphony_strategy must be called to persist locked_vars changes"
    )
    saved_locked = save_calls[0]["locked"]
    assert "trail_pct" in saved_locked, (
        "AC-6: 'trail_pct' must appear in the persisted locked_vars list"
    )
    assert "vol_scale" in saved_locked, (
        "AC-6: 'vol_scale' must appear in the persisted locked_vars list"
    )


# ---------------------------------------------------------------------------
# AC-7 — Autotuner parameters read-only in the modal HTML / JS
# ---------------------------------------------------------------------------


def test_symphony_settings_response_includes_parameters_key(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-7: GET /api/symphony-settings must return a 'parameters' key for the read-only kv list.

    The modal's Autotuner parameters section renders the current autotuner-owned
    values as a read-only list (no input elements).  The route must supply these
    values; missing them causes the section to render empty.
    """
    _mock_env(monkeypatch)
    resp = client.get("/api/symphony-settings/alpha_momentum")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "parameters" in body or "params" in body, (
        "AC-7: GET /api/symphony-settings must include 'parameters' (or 'params') key "
        "for the read-only autotuner parameters section."
    )


def test_modal_js_no_edit_input_for_autotuner_params():
    """AC-7: settings-modal.js must NOT create <input> elements for autotuner parameters.

    The autotuner parameters section is read-only.  Any edit control (input, select,
    contenteditable) in the parameters rendering path violates AC-7 and could allow
    operators to submit parameter values that override autotuner-owned settings.
    """
    js_path = _STATIC_DIR / "settings-modal.js"
    if not js_path.exists():
        pytest.skip("settings-modal.js not yet created")

    source = js_path.read_text(encoding="utf-8")

    # Extract the parameters / autotuner section of the JS (heuristic: look for
    # the block that renders tuning param rows).
    # A compliant implementation uses a read-only span/div per parameter, not an input.
    # We look for input/select/textarea inside a renderParameters / renderTuning / params section.
    autotuner_section_patterns = [
        r"renderParam",
        r"autotuner",
        r"tuning",
        r"parameters",
    ]
    lower = source.lower()
    section_present = any(p in lower for p in autotuner_section_patterns)

    if section_present:
        # Dangerous pattern: an <input> that appears close to the params section.
        # Allow type=hidden (for form values) but not text/number/range.
        editable_pattern = re.compile(
            r"""type\s*=\s*["'](text|number|range|checkbox)["']""",
            re.IGNORECASE,
        )
        # Find all input type references and see if they're near tuning param labels.
        for match in editable_pattern.finditer(source):
            ctx_start = max(0, match.start() - 400)
            ctx_end = min(len(source), match.end() + 400)
            ctx = source[ctx_start:ctx_end].lower()
            is_near_params = any(
                label in ctx
                for label in ["trail_pct", "tp_target", "vwap_bleed", "vol_scale",
                               "mc_prob", "para_ratchet", "tuning", "parameter"]
            )
            assert not is_near_params, (
                "AC-7: settings-modal.js must NOT render an editable <input> near the "
                "autotuner parameters section.  Autotuner params are read-only — the "
                "operator cannot override them via the modal."
            )


# ---------------------------------------------------------------------------
# AC-8 — AI advisor section: display-only, no apply button, empty state
# ---------------------------------------------------------------------------


def test_symphony_settings_response_includes_advisor_observations(
    client, monkeypatch
):
    """AC-8: GET /api/symphony-settings must return 'advisor_observations' for the modal's
    AI-advisor section.
    """
    _mock_env(monkeypatch)
    with patch.object(app_module, "database") as db_mock:
        db_mock.normalize_name.side_effect = (
            lambda n: (n or "").lower().replace(" ", "_").replace("-", "_")
        )
        db_mock.get_symphony_strategy.return_value = {
            "params": database.DEFAULT_STRATEGY.copy(),
            "locked_vars": [],
            "live_mode": False,
        }
        db_mock.get_advisor_observations_for_symphony.return_value = [
            {
                "variable": "trail_pct",
                "suggested": 18.0,
                "rationale": "Walk-forward suggests tighter stop.",
                "confidence": "medium",
            }
        ]

        resp = client.get("/api/symphony-settings/alpha_momentum")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "advisor_observations" in body or "advisor" in body, (
        "AC-8: GET /api/symphony-settings must include 'advisor_observations' "
        "(or 'advisor') key for the display-only AI-advisor section."
    )


def test_symphony_settings_advisor_empty_state_returns_empty_list(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-8: When no advisor observations exist, the response must include an empty list.

    The modal renders 'No advisor suggestions for this symphony yet.' for an
    empty list — it must NOT render the full card layout with no cards (invisible
    section header only) which would look broken.
    """
    _mock_env(monkeypatch)
    resp = client.get("/api/symphony-settings/alpha_momentum")
    if resp.status_code == 200:
        body = resp.get_json()
        advisor_key = "advisor_observations" if "advisor_observations" in body else "advisor"
        if advisor_key in body:
            obs = body[advisor_key]
            assert isinstance(obs, list), (
                f"AC-8: '{advisor_key}' must be a list; got {type(obs).__name__}"
            )


def test_modal_js_advisor_section_has_no_apply_button():
    """AC-8: settings-modal.js must NOT render an apply button in the advisor section.

    The advisor cards are display-only.  An apply button would allow operators
    to one-click overwrite autotuner parameters, violating the 'Advisory only'
    display contract.
    """
    js_path = _STATIC_DIR / "settings-modal.js"
    if not js_path.exists():
        pytest.skip("settings-modal.js not yet created")

    source = js_path.read_text(encoding="utf-8")
    lower = source.lower()

    # Only check if the advisor section is present
    if "advisor" not in lower:
        return

    # Find the advisor rendering block and verify no 'apply' button exists within it.
    # Strategy: look for 'apply' within 200 chars of 'advisor' card rendering.
    advisor_idx = lower.find("advisor")
    while advisor_idx != -1:
        ctx = lower[advisor_idx: advisor_idx + 600]
        assert "apply" not in ctx, (
            "AC-8: The advisor section in settings-modal.js must not contain an 'apply' "
            "button or 'apply' action.  Advisor cards are display-only — AC spec says "
            "'no apply button' explicitly."
        )
        advisor_idx = lower.find("advisor", advisor_idx + 1)


def test_modal_html_advisor_section_has_no_apply_button(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-8: The rendered modal HTML (via /api/symphony-settings) must not contain an 'apply'
    action in the advisor section.

    This verifies the server-rendered path (if server-side rendering is used)
    in addition to the JS path.
    """
    _mock_env(monkeypatch)
    resp = client.get("/api/symphony-settings/alpha_momentum")
    if resp.status_code != 200:
        pytest.skip("Route not yet implemented")
    # If the response is HTML (server-side rendered), check for apply
    ct = resp.content_type
    if "html" in ct:
        html = resp.get_data(as_text=True)
        # Find the advisor section and verify no apply button
        advisor_idx = html.lower().find("advisor")
        if advisor_idx != -1:
            ctx = html[advisor_idx: advisor_idx + 800].lower()
            assert "apply" not in ctx, (
                "AC-8: The advisor section must not have an 'apply' button. "
                "Display-only means no actionable controls."
            )


# ---------------------------------------------------------------------------
# AC-12 — Save-error response contract
# ---------------------------------------------------------------------------


def test_post_symphony_settings_returns_error_on_db_failure(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-12: When the DB write fails, the POST must return a non-2xx status with an error body.

    The modal renders a 'save-error' state when the save fails.  If the route
    swallows the exception and returns 200 with no error flag, the modal will
    silently show 'saved' while the change was NOT persisted.
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)

    def raise_on_save(*_a, **_kw):
        raise RuntimeError("Simulated DB lock — EOD cycle running")

    with (
        patch.object(app_module.database, "set_symphony_live_mode", side_effect=raise_on_save),
        patch.object(app_module.database, "save_symphony_strategy", side_effect=raise_on_save),
    ):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={"live_mode": False},
            content_type="application/json",
        )

    assert resp.status_code >= 400 or (
        resp.status_code == 200
        and resp.get_json() is not None
        and resp.get_json().get("status") == "error"
    ), (
        f"AC-12: POST /api/symphony-settings must return either 4xx/5xx or "
        f"200 with status='error' when the DB write fails. "
        f"Got status={resp.status_code}, body={resp.get_json()!r}. "
        "The save-error modal state depends on this signal."
    )


# ---------------------------------------------------------------------------
# AC-4 + AC-5 — Banner presence in GET response
# ---------------------------------------------------------------------------


def test_global_off_flag_present_in_symphony_settings_response(
    client, mock_db_live_symphony, monkeypatch
):
    """AC-4: When LIVE_EXECUTION=False, the response must make global_live=False
    so the JS can render the caution banner.
    """
    _mock_env(monkeypatch, live_execution="False")
    resp = client.get("/api/symphony-settings/alpha_momentum")
    if resp.status_code != 200:
        pytest.skip("Route not yet implemented")
    body = resp.get_json()
    assert "global_live" in body, (
        "AC-4: 'global_live' key must be present in /api/symphony-settings response "
        "so the JS modal can show the caution banner when global execution is off."
    )
    assert not body["global_live"], (
        "AC-4: global_live must be False/falsy when LIVE_EXECUTION=False."
    )


def test_both_live_flags_true_in_symphony_settings_response(
    client, mock_db_live_symphony, monkeypatch
):
    """AC-5: When both LIVE_EXECUTION=True and live_mode=True, both flags must be true.

    This is the condition that triggers the danger banner in the modal.
    """
    _mock_env(monkeypatch, live_execution="True")
    resp = client.get("/api/symphony-settings/alpha_momentum")
    if resp.status_code != 200:
        pytest.skip("Route not yet implemented")
    body = resp.get_json()
    assert body.get("global_live"), (
        "AC-5: global_live must be True when LIVE_EXECUTION=True"
    )
    assert body.get("live_mode"), (
        "AC-5: live_mode must be True for the mock_db_live_symphony fixture"
    )


# ---------------------------------------------------------------------------
# Structural — symphony-settings route must exist
# ---------------------------------------------------------------------------


def test_get_symphony_settings_route_exists(
    client, mock_db_one_symphony, monkeypatch
):
    """GET /api/symphony-settings/<sym> route must be registered (404 means missing route)."""
    _mock_env(monkeypatch)
    resp = client.get("/api/symphony-settings/alpha_momentum")
    assert resp.status_code != 404, (
        "GET /api/symphony-settings/<sym> returned 404 — the route is not registered "
        "in app.py.  This is the endpoint the modal JS calls to populate all modal state."
    )


def test_post_symphony_settings_route_exists(
    client, mock_db_one_symphony, monkeypatch
):
    """POST /api/symphony-settings/<sym> route must be registered."""
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    resp = client.post(
        "/api/symphony-settings/alpha_momentum",
        json={"live_mode": False},
        content_type="application/json",
    )
    assert resp.status_code != 404, (
        "POST /api/symphony-settings/<sym> returned 404 — the route is not registered. "
        "The modal save button calls this endpoint."
    )


# ---------------------------------------------------------------------------
# AC-3 robustness — confirm gate must reject non-bool live_mode values
# (reviewer finding: string "true" falls through both branches as a no-op)
# ---------------------------------------------------------------------------


def test_post_live_mode_string_true_does_not_silently_arm_live(
    client, mock_db_one_symphony, monkeypatch
):
    """AC-3 robustness: POST with live_mode='true' (string) must NOT silently arm live mode.

    The confirm gate checks `live_mode_raw is True or live_mode_raw == 1`.
    A crafted request sending the JSON string "true" fails both checks and
    falls through as a silent no-op — set_symphony_live_mode is never called,
    no 400 is returned, and the route returns 200 as if the save succeeded.

    This is an API invariant gap: the confirm gate is a server-side safety
    boundary, not just a JS convention.  A string "true" from a crafted
    request must either:
      (a) be rejected with 400 (invalid type for a boolean field), or
      (b) be treated as truthy and rejected with 400 because confirm is absent.

    It must NEVER silently succeed as a no-op while returning 200.

    Reviewer finding from quant-code-reviewer review of 49de1af.
    """
    _disable_csrf(monkeypatch)
    _mock_env(monkeypatch)
    set_live_calls = []

    def fake_set_live(name, live, operator):
        set_live_calls.append({"name": name, "live": live})

    with patch.object(app_module.database, "set_symphony_live_mode", side_effect=fake_set_live):
        resp = client.post(
            "/api/symphony-settings/alpha_momentum",
            json={"live_mode": "true"},  # string, not bool — crafted request
            content_type="application/json",
        )

    # The response must NOT be a silent 200 success while doing nothing.
    # Either 400 (type rejected) or 200 with set_symphony_live_mode NOT called
    # with live=1 is acceptable. A 200 that silently no-ops is the failure case.
    if resp.status_code == 200:
        # If 200, set_symphony_live_mode must not have been called with live=1
        # (i.e., the string was not treated as a live-arm without confirm).
        live_armed = any(c["live"] in (1, True) for c in set_live_calls)
        assert not live_armed, (
            "AC-3: POST with live_mode='true' (string) silently called "
            "set_symphony_live_mode(live=1) without requiring confirm. "
            "The confirm gate is an API invariant — non-bool values must not bypass it."
        )
        # Additionally: a 200 with zero calls is a silent no-op — the route
        # must signal the caller that the string was unrecognized/invalid.
        assert resp.get_json() is not None, "Response must have a JSON body"
        # A silent no-op that returns {"status": "success"} is incorrect —
        # the caller would think live_mode was saved when it was not.
        body = resp.get_json()
        if len(set_live_calls) == 0:
            # No DB call was made — the response must not claim success on live_mode
            # (it may claim success if only locked_vars was processed, but since
            # no locked_vars were sent, a success here is misleading).
            # The route should return 400 for an unrecognized live_mode type.
            assert body.get("status") != "success" or resp.status_code == 400, (
                "AC-3: POST with live_mode='true' (string) returned 200 status='success' "
                "with no DB calls made. This is a silent no-op on a real-money toggle "
                "endpoint. The route must reject unrecognized live_mode types with 400."
            )
    else:
        # Any non-200 is acceptable (400 = explicit type rejection)
        assert resp.status_code in (400, 422), (
            f"AC-3: POST with live_mode='true' (string) returned {resp.status_code}; "
            f"expected 400 (type rejection) or 200 with no live-arm. "
            f"Got: {resp.get_json()!r}"
        )
