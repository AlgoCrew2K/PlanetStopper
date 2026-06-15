"""
RED tests — Cycle-6: Settings page (AC-S.8 + chrome NITs).

Surfaces covered:
  AC-S.8.1  /settings HTML route exists and renders settings.html
  AC-S.8.2  /api/settings GET returns `secrets` key (masked, write-only display)
  AC-S.8.3  /api/settings GET returns `param_meta` key with algorithm param bounds/help
  AC-S.8.4  /api/settings GET returns `symphony_overrides` key (keyed by norm_name)
  AC-S.8.5  settings.html includes top-bar chrome (_chrome.html) with active_route='settings'
  AC-S.8.6  settings.html has vertical side nav with 4 sections
  AC-S.8.7  settings.html LIVE_EXECUTION toggle uses danger/neg token when LIVE
  AC-S.8.8  settings.html credential fields are type=password (masked) with show/hide
  AC-S.8.9  settings.js exists and loads; parses PARAM_META keys from API response
  AC-S.8.10 settings.js renderMasterControls binds LIVE_EXECUTION to danger styling
  AC-S.8.11 settings.js renderCredentials renders type=password inputs
  AC-S.8.12 settings.js renderSymphonyOverrides renders lock toggle per param
  AC-S.8.13 chrome: Settings nav link renders with aria-current="page" on /settings route
  AC-S.8.14 chrome: History nav link renders with aria-current="page" on /history route
  AC-S.8.15 /api/settings GET param_meta keys match DEFAULT_STRATEGY keys from database
  AC-S.8.16 /api/settings GET param_meta entries have `help` and `unit` fields
  AC-S.8.17 settings.html Save/Discard buttons are present in DOM
  AC-S.8.18 settings.js renderAlgorithmParams renders one card per PARAM_META key
  AC-S.8.19 POST /api/settings symphonies payload writes locked_vars correctly
  AC-S.8.20 settings.html does not contain raw hex colours (no-bare-hex rule)
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import patch

import pytest

import app as app_module
import database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"

PARAM_META_KEYS = [
    "TRIGGER_THRESHOLD_PCT",
    "TAKE_PROFIT_MC_PCT",
    "MAX_SQUEEZE_FLOOR",
    "VWAP_CROSS_HWM_PCT",
    "VWAP_BLEED_MULTIPLIER",
    "VWAP_BLEED_TICKS",
    "PARABOLIC_VELOCITY_THRESHOLD",
    "MAX_PARABOLIC_SQUEEZE",
]

SECRET_KEYS = [
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
]

_BARE_HEX_RE = re.compile(r'(?<!["\w-])#[0-9A-Fa-f]{3,8}\b')


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_db_empty(monkeypatch):
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_symphony_strategy.return_value = {
            "params": database.DEFAULT_STRATEGY.copy(),
            "locked_vars": ["TRIGGER_THRESHOLD_PCT"],
        }
        yield db_mock


@pytest.fixture
def mock_db_with_symphony(monkeypatch):
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {"sym_abc": {"name": "My Symphony", "position": 0.5}}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_symphony_strategy.return_value = {
            "params": {"TRIGGER_THRESHOLD_PCT": 20.0, "TAKE_PROFIT_MC_PCT": 5.0},
            "locked_vars": ["TRIGGER_THRESHOLD_PCT"],
        }
        yield db_mock


def _mock_env(monkeypatch, extra=None):
    env = {
        "LIVE_EXECUTION": "False",
        "EXECUTION_START_TIME": "09:30",
        "EXIT_AUTHORITY": "per_symphony",
        "COMPOSER_KEY_ID": "test-ck",
        "COMPOSER_SECRET": "test-cs",
        "ACCOUNT_INDIVIDUAL": "test-ai",
        "ACCOUNT_ROTH": "test-ar",
        "ACCOUNT_TRAD": "test-at",
        "ALPACA_KEY": "test-ak",
        "ALPACA_SECRET": "test-as",
        "DISCORD_WEBHOOK_URL": "https://discord.test/webhook",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    if extra:
        env.update(extra)
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: env)
    return env


def _extract_js_function(source: str, fn_name: str) -> str:
    """Brace-counting function body extractor — same pattern as prior cycles."""
    stripped = re.sub(r"//[^\n]*", "", source)
    start = re.search(r"function\s+" + re.escape(fn_name) + r"\s*\(", stripped)
    if start is None:
        return ""
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                return stripped[brace_start + 1 : i]
        i += 1
    return ""


# ---------------------------------------------------------------------------
# AC-S.8.1 — /settings HTML route
# ---------------------------------------------------------------------------


def test_settings_route_exists(client, mock_db_empty, monkeypatch):
    """GET /settings must return 200 (route registered in app.py)."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200, (
        f"GET /settings must return 200; got {resp.status_code}. Route is missing from app.py."
    )


def test_settings_route_renders_html(client, mock_db_empty, monkeypatch):
    """GET /settings must return text/html content-type."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    ct = resp.content_type
    assert "text/html" in ct, f"GET /settings must return HTML; got content-type: {ct}"


# ---------------------------------------------------------------------------
# AC-S.8.2 — /api/settings GET `secrets` key
# ---------------------------------------------------------------------------


def test_api_settings_get_has_secrets_key(client, mock_db_empty, monkeypatch):
    """/api/settings GET must include a `secrets` key in the JSON response."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "secrets" in body, (
        "GET /api/settings must include a 'secrets' key. "
        "Design handoff separates secrets from globals for the Credentials panel."
    )


def test_api_settings_secrets_are_masked(client, mock_db_empty, monkeypatch):
    """/api/settings GET `secrets` values must all be empty strings (write-only)."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "secrets" in body, "Response must have 'secrets' key for this test to be meaningful"
    secrets = body["secrets"]
    assert len(secrets) > 0, "secrets dict must be non-empty (must include credential keys)"
    for key in SECRET_KEYS:
        if key in secrets:
            assert secrets[key] == "", (
                f"secrets.{key} must be masked to '' in GET response; got: {secrets[key]!r}"
            )


def test_api_settings_secrets_has_all_credential_keys(client, mock_db_empty, monkeypatch):
    """/api/settings GET `secrets` must include all 9 credential keys."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    secrets = resp.get_json().get("secrets", {})
    for key in SECRET_KEYS:
        assert key in secrets, (
            f"secrets.{key} must be present in GET /api/settings response "
            "(Credentials panel needs all key names to render field labels)"
        )


# ---------------------------------------------------------------------------
# AC-S.8.3 — /api/settings GET `param_meta`
# ---------------------------------------------------------------------------


def test_api_settings_get_has_param_meta_key(client, mock_db_empty, monkeypatch):
    """/api/settings GET must include a `param_meta` key."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "param_meta" in body, (
        "GET /api/settings must include 'param_meta' key. "
        "Algorithm parameters panel needs bounds + help text from the server."
    )


def test_api_settings_param_meta_has_all_algo_keys(client, mock_db_empty, monkeypatch):
    """/api/settings GET `param_meta` must include every algorithm param key."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    param_meta = resp.get_json().get("param_meta", {})
    for key in PARAM_META_KEYS:
        assert key in param_meta, (
            f"param_meta must include '{key}'; missing. All 8 DEFAULT_STRATEGY keys need metadata."
        )


def test_api_settings_param_meta_entries_have_help_and_unit(client, mock_db_empty, monkeypatch):
    """/api/settings GET each `param_meta` entry must have `help` and `unit` fields."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "param_meta" in body, (
        "Response must have 'param_meta' key for this test to be meaningful"
    )
    param_meta = body["param_meta"]
    assert len(param_meta) > 0, "param_meta must be non-empty (must include algorithm param keys)"
    for key in PARAM_META_KEYS:
        assert key in param_meta, f"param_meta must include '{key}'"
        entry = param_meta[key]
        assert "help" in entry, f"param_meta.{key} must have 'help' field"
        assert "unit" in entry, f"param_meta.{key} must have 'unit' field"
        assert isinstance(entry["help"], str) and len(entry["help"]) > 0, (
            f"param_meta.{key}.help must be a non-empty string"
        )


# ---------------------------------------------------------------------------
# AC-S.8.4 — /api/settings GET `symphony_overrides`
# ---------------------------------------------------------------------------


def test_api_settings_get_has_symphony_overrides_key(client, mock_db_empty, monkeypatch):
    """/api/settings GET must include a `symphony_overrides` key."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "symphony_overrides" in body, (
        "GET /api/settings must include 'symphony_overrides' key. "
        "Symphony overrides section needs this for the split pane."
    )


def test_api_settings_symphony_overrides_has_params_and_locked_vars(
    client, mock_db_with_symphony, monkeypatch
):
    """`symphony_overrides` entries must have `params` and `locked_vars` fields."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "symphony_overrides" in body, (
        "Response must have 'symphony_overrides' key for this test to be meaningful"
    )
    overrides = body["symphony_overrides"]
    assert len(overrides) > 0, (
        "symphony_overrides must be non-empty when state has symphonies "
        "(mock_db_with_symphony provides one)"
    )
    for sym_name, data in overrides.items():
        assert "params" in data, f"symphony_overrides.{sym_name} must have 'params' field"
        assert "locked_vars" in data, f"symphony_overrides.{sym_name} must have 'locked_vars' field"
        assert isinstance(data["locked_vars"], list), (
            f"symphony_overrides.{sym_name}.locked_vars must be a list"
        )


# ---------------------------------------------------------------------------
# AC-S.8.5 — settings.html includes chrome with active_route='settings'
# ---------------------------------------------------------------------------


def test_settings_html_includes_chrome(client, mock_db_empty, monkeypatch):
    """GET /settings HTML must include the top-bar chrome (data-testid=top-bar)."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-testid="top-bar"' in html, (
        "settings.html must include _chrome.html top-bar. "
        "{% include '_chrome.html' %} with active_route='settings' is missing."
    )


def test_settings_html_nav_settings_active(client, mock_db_empty, monkeypatch):
    """GET /settings HTML must show Settings nav link with aria-current=page."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'aria-current="page"' in html, (
        "settings.html must render the Settings nav link with aria-current='page'."
    )


# ---------------------------------------------------------------------------
# AC-S.8.6 — Vertical side nav with 4 sections
# ---------------------------------------------------------------------------


def test_settings_html_has_vertical_sidenav(client, mock_db_empty, monkeypatch):
    """settings.html must have a vertical side nav element."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert (
        'data-testid="settings-sidenav"' in html
        or "settings-nav" in html
        or (
            "Master" in html
            and "Algorithm" in html
            and "Credentials" in html
            and "Symphony" in html
        )
    ), (
        "settings.html must render a vertical side nav with Master / Algorithm / "
        "Credentials / Symphony sections."
    )


def test_settings_html_sidenav_has_four_sections(client, mock_db_empty, monkeypatch):
    """settings.html side nav must have all 4 required section labels."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for label in ["Master", "Algorithm", "Credentials", "Symphony"]:
        assert label in html, f"settings.html side nav must include '{label}' section label."


# ---------------------------------------------------------------------------
# AC-S.8.7 — LIVE_EXECUTION danger styling
# ---------------------------------------------------------------------------


def test_settings_html_live_execution_uses_neg_token_when_live(client, mock_db_empty, monkeypatch):
    """settings.html Master section must reference --studio-neg for LIVE danger styling."""
    _mock_env(monkeypatch, extra={"LIVE_EXECUTION": "True"})
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "--studio-neg" in html, (
        "settings.html must use --studio-neg token for LIVE execution danger styling. "
        "No bare hex colours allowed."
    )


def test_settings_html_live_execution_field_present(client, mock_db_empty, monkeypatch):
    """settings.html must render a LIVE_EXECUTION toggle or field."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "LIVE_EXECUTION" in html or "live-execution" in html or "Live execution" in html, (
        "settings.html must render a LIVE_EXECUTION control in the Master section."
    )


# ---------------------------------------------------------------------------
# AC-S.8.8 — Credential fields are type=password
# ---------------------------------------------------------------------------


def test_settings_html_credential_inputs_are_password_type(client, mock_db_empty, monkeypatch):
    """settings.html Credentials section must have type=password inputs."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'type="password"' in html or "type='password'" in html, (
        "settings.html Credentials section must have type=password inputs. "
        "Credential values must be write-only masked fields."
    )


def test_settings_html_credential_section_has_show_hide(client, mock_db_empty, monkeypatch):
    """settings.html Credentials section must have show/hide reveal buttons."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "show" in html.lower() and "hide" in html.lower(), (
        "settings.html Credentials section must have show/hide toggle for password fields."
    )


# ---------------------------------------------------------------------------
# AC-S.8.9 — settings.js exists
# ---------------------------------------------------------------------------


def test_settings_js_file_exists():
    """static/settings.js must exist."""
    js_path = _STATIC_DIR / "settings.js"
    assert js_path.exists(), (
        f"static/settings.js must be created for Cycle-6 Settings. File not found at: {js_path}"
    )


def test_settings_js_references_param_meta_keys():
    """static/settings.js must reference the 8 PARAM_META algorithm param keys."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    for key in PARAM_META_KEYS:
        assert key in source, (
            f"settings.js must reference '{key}' (PARAM_META algorithm param key). "
            "The Algorithm parameters section renders cards for each key."
        )


# ---------------------------------------------------------------------------
# AC-S.8.10 — settings.js renderMasterControls danger styling
# ---------------------------------------------------------------------------


def test_settings_js_render_master_references_studio_neg():
    """settings.js master controls section must reference --studio-neg for LIVE state."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    assert "--studio-neg" in source, (
        "settings.js must reference --studio-neg CSS token for LIVE danger styling. "
        "No bare hex colours for danger states."
    )


def test_settings_js_master_section_references_live_execution():
    """settings.js must reference LIVE_EXECUTION in master controls logic."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    assert "LIVE_EXECUTION" in source, (
        "settings.js must read and render LIVE_EXECUTION from the globals payload."
    )


# ---------------------------------------------------------------------------
# AC-S.8.11 — settings.js renderCredentials uses password inputs
# ---------------------------------------------------------------------------


def test_settings_js_renders_password_type_inputs():
    """settings.js Credentials render must produce type=password inputs."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    assert "'password'" in source or '"password"' in source, (
        "settings.js must set input type to 'password' for credential fields. "
        "Secrets must not be visible in plain text by default."
    )


def test_settings_js_credentials_has_reveal_toggle():
    """settings.js must have show/hide reveal logic for password inputs."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    assert ("'text'" in source or '"text"' in source) and (
        "'password'" in source or '"password"' in source
    ), (
        "settings.js must toggle between type='password' and type='text' for show/hide. "
        "CredField.reveal toggle switches the input type."
    )


# ---------------------------------------------------------------------------
# AC-S.8.12 — settings.js renderSymphonyOverrides has lock toggle per param
# ---------------------------------------------------------------------------


def test_settings_js_symphony_overrides_references_locked_vars():
    """settings.js must reference locked_vars for per-param lock toggle rendering."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    assert "locked_vars" in source or "locked" in source, (
        "settings.js must reference 'locked_vars' for the lock toggle in Symphony overrides. "
        "Each param row needs a lock/unlock button."
    )


def test_settings_js_symphony_overrides_lock_uses_warn_token():
    """settings.js lock toggle must use --studio-warn token for locked state."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    assert "--studio-warn" in source, (
        "settings.js must use --studio-warn CSS token for the locked param indicator. "
        "Design handoff shows warn colour for locked params."
    )


# ---------------------------------------------------------------------------
# AC-S.8.13 — chrome: Settings nav link aria-current on /settings
# ---------------------------------------------------------------------------


def test_chrome_settings_nav_link_active_on_settings_route(client, mock_db_empty, monkeypatch):
    """GET /settings must render Settings nav link with aria-current=page."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # aria-current="page" must be on the Settings link, not just anywhere
    settings_block = html[html.find("/settings") :]
    assert 'aria-current="page"' in settings_block[:300], (
        "The /settings nav link must carry aria-current='page' when active_route='settings'."
    )


# ---------------------------------------------------------------------------
# AC-S.8.14 — chrome: History nav link aria-current on /history route
# ---------------------------------------------------------------------------


def test_chrome_history_nav_link_active_on_history_route(client, mock_db_empty, monkeypatch):
    """GET /history must render History nav link with aria-current=page (regression guard)."""
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.get_exit_records.return_value = []
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        _mock_env(monkeypatch)
        resp = client.get("/history")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    history_block = html[html.find("/history") :]
    assert 'aria-current="page"' in history_block[:300], (
        "The /history nav link must carry aria-current='page' when active_route='history'."
    )


# ---------------------------------------------------------------------------
# AC-S.8.15 — param_meta keys match DEFAULT_STRATEGY keys
# ---------------------------------------------------------------------------


def test_api_settings_param_meta_keys_match_default_strategy(client, mock_db_empty, monkeypatch):
    """/api/settings param_meta keys must exactly match database.DEFAULT_STRATEGY keys."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    param_meta = resp.get_json().get("param_meta", {})
    expected = set(database.DEFAULT_STRATEGY.keys())
    actual = set(param_meta.keys())
    missing = expected - actual
    assert not missing, (
        f"param_meta is missing keys that exist in DEFAULT_STRATEGY: {missing}. "
        "Every algorithm param needs metadata."
    )


# ---------------------------------------------------------------------------
# AC-S.8.16 — param_meta `help` and `unit` validation (already in AC-S.8.3,
#             but this tests the `kind` field for type-aware rendering)
# ---------------------------------------------------------------------------


def test_api_settings_param_meta_entries_have_kind_field(client, mock_db_empty, monkeypatch):
    """/api/settings param_meta entries must have a `kind` field for type-aware rendering."""
    _mock_env(monkeypatch)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "param_meta" in body, (
        "Response must have 'param_meta' key for this test to be meaningful"
    )
    param_meta = body["param_meta"]
    assert len(param_meta) > 0, "param_meta must be non-empty"
    valid_kinds = {"pct", "mult", "int", "decimal"}
    for key in PARAM_META_KEYS:
        assert key in param_meta, f"param_meta must include '{key}'"
        entry = param_meta[key]
        assert "kind" in entry, (
            f"param_meta.{key} must have 'kind' field ('pct', 'mult', 'int', 'decimal')"
        )
        assert entry["kind"] in valid_kinds, (
            f"param_meta.{key}.kind must be one of {valid_kinds}; got: {entry['kind']!r}"
        )


# ---------------------------------------------------------------------------
# AC-S.8.17 — Save/Discard buttons present in DOM
# ---------------------------------------------------------------------------


def test_settings_html_has_save_button(client, mock_db_empty, monkeypatch):
    """settings.html must have a Save changes button."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Save" in html, (
        "settings.html must render a 'Save changes' button in the page header actions."
    )


def test_settings_html_has_discard_button(client, mock_db_empty, monkeypatch):
    """settings.html must have a Discard changes button."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Discard" in html or "discard" in html.lower(), (
        "settings.html must render a 'Discard changes' button in the page header actions."
    )


# ---------------------------------------------------------------------------
# AC-S.8.18 — settings.js renders one card per PARAM_META key
# ---------------------------------------------------------------------------


def test_settings_js_algorithm_section_iterates_param_meta():
    """settings.js Algorithm section must iterate over PARAM_META (not hardcode cards)."""
    js_path = _STATIC_DIR / "settings.js"
    if not js_path.exists():
        pytest.skip("settings.js not yet created")
    source = js_path.read_text(encoding="utf-8")
    # Must reference param_meta or PARAM_META from payload, not hardcode all 8 keys inline
    assert "param_meta" in source or "PARAM_META" in source, (
        "settings.js must reference param_meta (from API response or local const) "
        "to drive the Algorithm parameters card grid."
    )
    # At minimum it should render a card per key — check for map/forEach/keys pattern
    assert (
        ".map(" in source
        or "forEach(" in source
        or "Object.keys(" in source
        or "Object.entries(" in source
    ), (
        "settings.js Algorithm section must use map/forEach/Object.keys/entries to iterate "
        "param_meta keys — not hardcoded per-param branches."
    )


# ---------------------------------------------------------------------------
# AC-S.8.19 — POST /api/settings writes locked_vars correctly
# ---------------------------------------------------------------------------


def test_post_settings_symphony_locked_vars_written(client, mock_db_empty, monkeypatch):
    """POST /api/settings must persist locked_vars for symphony overrides."""
    _mock_env(monkeypatch)
    save_calls = []

    def fake_save(name, params, locked):
        save_calls.append({"name": name, "params": params, "locked": locked})

    with patch.object(app_module.database, "save_symphony_strategy", side_effect=fake_save):
        payload = {
            "globals": {},
            "symphonies": {
                "my_symphony": {
                    "params": {"TRIGGER_THRESHOLD_PCT": 20.0},
                    "locked_vars": ["TRIGGER_THRESHOLD_PCT", "MAX_SQUEEZE_FLOOR"],
                }
            },
        }
        resp = client.post("/api/settings", json=payload)

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"
    assert len(save_calls) == 1, "save_symphony_strategy must be called once"
    call = save_calls[0]
    assert "TRIGGER_THRESHOLD_PCT" in call["locked"], (
        "locked_vars must be persisted; TRIGGER_THRESHOLD_PCT missing from saved locked list"
    )
    assert "MAX_SQUEEZE_FLOOR" in call["locked"], (
        "locked_vars must be persisted; MAX_SQUEEZE_FLOOR missing from saved locked list"
    )


# ---------------------------------------------------------------------------
# AC-S.8.20 — settings.html no bare hex colours
# ---------------------------------------------------------------------------


def test_settings_html_no_bare_hex_colours(client, mock_db_empty, monkeypatch):
    """settings.html must not contain bare hex colour values — use CSS var() tokens only."""
    _mock_env(monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Strip script/style blocks inserted by js — only check template HTML
    # (allow #fff and #000 which are canonical in tweaks.css thumb; flag everything else)
    stripped = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.DOTALL)
    matches = _BARE_HEX_RE.findall(stripped)
    allowed = {"#fff", "#000", "#ffffff", "#000000"}
    violations = [m for m in matches if m.lower() not in allowed]
    assert not violations, (
        f"settings.html contains bare hex colour(s) outside var() tokens: {violations}. "
        "All colours must use --studio-* CSS custom properties."
    )
