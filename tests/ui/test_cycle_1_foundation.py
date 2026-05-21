"""
Cycle 1 — Foundation RED tests.

Contract doc: docs/handoff/cycle-1-contract.md
Design source: .design-handoff/project/chrome.jsx, tweaks-panel.jsx, studio.jsx

Tests assert:
  AC-C1.1  Every existing route renders HTTP 200 + studio chrome nav landmark
  AC-C1.2  static/tokens.css exists with required --studio-* CSS variables
  AC-C1.3  static/tweaks.js exists and seeds studioTweaks defaults
  AC-C1.4  /api/state response contains meta{mode, system_online, tracked, armed, triggered}
  AC-C1.5  meta.mode is "LIVE" / "DRY RUN" reflecting live_mode flag
  AC-C1.6  meta.next_run is "mm:ss" formatted string
  AC-C1.7  Nav contains links to all 5 Studio routes
  AC-C1.8  Active-route tab has aria-current="page" or data-active="true"
  AC-C1.9  Tweaks button present with data-testid="tweaks-btn"
  AC-C1.10 No raw hex in rendered templates outside tokens.css (sampling test)
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent


def _state_with_live_mode(
    live_mode: bool, *, n_symphonies: int = 0, n_armed: int = 0, n_triggered: int = 0
):
    """Return a patched dotenv_values callable that drives meta field computation."""
    state: dict = {}
    for i in range(n_symphonies):
        sym_id = f"sym_{i}"
        state[sym_id] = {
            "name": f"Symphony {i}",
            "account": "acc_test",
            "current_return": 0.5,
            "current_value": 1000.0,
            "armed": i < n_armed,
            "triggered": i < n_triggered,
        }
    return state, live_mode


# ---------------------------------------------------------------------------
# AC-C1.1 — Every existing route returns 200 + studio chrome nav landmark
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_existing_routes_return_200(client, route):
    """All existing routes must return HTTP 200 after Foundation is built."""
    resp = client.get(route)
    assert resp.status_code == 200


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_existing_routes_contain_studio_nav(client, route):
    """Every route must include the Studio chrome nav landmark.

    Implementer must add data-testid='studio-nav' to the shared _chrome.html include
    (or equivalent landmark). This test verifies the include is rendered on every page.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="studio-nav"' in html, (
        f'Route {route}: expected data-testid="studio-nav" in rendered HTML. '
        "The Studio chrome _chrome.html include must be added to every template."
    )


# ---------------------------------------------------------------------------
# AC-C1.2 — tokens.css exists with required CSS custom properties
# ---------------------------------------------------------------------------


def test_tokens_css_file_exists():
    """static/tokens.css must exist."""
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    assert tokens_path.exists(), (
        "static/tokens.css not found. Implementer must create this file with --studio-* CSS variables."
    )


@pytest.mark.parametrize(
    "var_name",
    [
        "--studio-bg",
        "--studio-paper",
        "--studio-ink",
        "--studio-accent",
        "--studio-pos",
        "--studio-neg",
        "--studio-rule",
    ],
)
def test_tokens_css_contains_required_variables(var_name):
    """tokens.css must declare each required --studio-* CSS variable."""
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    assert var_name in content, (
        f"tokens.css missing required CSS variable {var_name}. "
        "All palette tokens from studioPalette() must be declared."
    )


def test_tokens_css_no_hardcoded_hex_in_root():
    """tokens.css :root block must not contain bare hex values outside variable declarations.

    Hex values are ONLY permitted as the assigned value of a --studio-* variable,
    not sprinkled as raw color values in selectors or inline styles.
    This test flags any line that has a hex color that is NOT the RHS of a CSS var assignment.
    """
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    # Flag any line with a hex color that is NOT the RHS of a CSS variable declaration.
    # Allow: "  --studio-bg: #f4efe2;"
    # Disallow: "color: #abc123;" bare hex in a rule block
    lines_with_bare_hex = [
        line.strip()
        for line in content.splitlines()
        if re.search(r"#[0-9a-fA-F]{3,8}", line)
        and not re.match(r"\s*--[\w-]+\s*:", line)
        and not line.strip().startswith("//")
        and not line.strip().startswith("/*")
        and not line.strip().startswith("*")  # inside block comment
        and not line.strip().endswith("*/")   # end of block comment
    ]
    assert not lines_with_bare_hex, (
        f"tokens.css contains hex colors outside CSS variable declarations: {lines_with_bare_hex}"
    )


# ---------------------------------------------------------------------------
# AC-C1.3 — tweaks.js exists and seeds localStorage defaults
# ---------------------------------------------------------------------------


def test_tweaks_js_file_exists():
    """static/tweaks.js must exist."""
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    assert tweaks_path.exists(), "static/tweaks.js not found. Implementer must create this file."


@pytest.mark.parametrize(
    "default_key", ["theme", "density", "accent", "typeface", "mathOverlays", "numFormat"]
)
def test_tweaks_js_references_all_default_keys(default_key):
    """tweaks.js must reference all 6 tweak default keys."""
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert default_key in content, (
        f"tweaks.js missing reference to default key '{default_key}'. "
        "All 6 tweak keys from TWEAK_DEFAULTS must be managed by tweaks.js."
    )


def test_tweaks_js_references_localstorage():
    """tweaks.js must use localStorage to persist tweak state."""
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "localStorage" in content, (
        "tweaks.js must persist tweaks to localStorage. "
        "The key should be 'alphabot_tweaks' or similar."
    )


def test_tweaks_css_file_exists():
    """static/tweaks.css must exist (slide-over panel styles)."""
    tweaks_css_path = PROJECT_ROOT / "static" / "tweaks.css"
    assert tweaks_css_path.exists(), (
        "static/tweaks.css not found. Implementer must create slide-over panel styles."
    )


def test_tweaks_css_contains_panel_class():
    """tweaks.css must contain the tweaks panel container class."""
    tweaks_css_path = PROJECT_ROOT / "static" / "tweaks.css"
    if not tweaks_css_path.exists():
        pytest.skip("tweaks.css does not exist yet")
    content = tweaks_css_path.read_text(encoding="utf-8")
    # The panel needs a recognizable container class — twk-panel (from design) or studio-tweaks-panel
    assert any(
        cls in content for cls in [".twk-panel", ".studio-tweaks-panel", "#studio-tweaks"]
    ), (
        "tweaks.css must define the tweaks slide-over panel container class (.twk-panel or equivalent)."
    )


# ---------------------------------------------------------------------------
# AC-C1.4 — /api/state contains meta object with required sub-keys
# ---------------------------------------------------------------------------


def test_api_state_contains_meta_key(client):
    """GET /api/state must return a JSON body with a 'meta' top-level key."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "meta" in data, (
        "/api/state response missing 'meta' key. "
        "Implementer must add the meta object (see cycle-1-contract.md)."
    )


@pytest.mark.parametrize("sub_key", ["mode", "system_online", "tracked", "armed", "triggered"])
def test_api_state_meta_contains_required_subkeys(client, sub_key):
    """GET /api/state meta object must contain all required sub-keys."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    assert sub_key in meta, (
        f"/api/state meta missing sub-key '{sub_key}'. See cycle-1-contract.md EXTEND decisions."
    )


def test_api_state_meta_tracked_is_int(client):
    """meta.tracked must be an integer count of tracked symphonies."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "tracked" not in meta:
        pytest.fail("meta.tracked missing from /api/state response")
    assert isinstance(meta["tracked"], int), (
        f"meta.tracked must be int, got {type(meta['tracked'])}"
    )


def test_api_state_meta_armed_is_int(client):
    """meta.armed must be an integer count of armed symphonies."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "armed" not in meta:
        pytest.fail("meta.armed missing from /api/state response")
    assert isinstance(meta["armed"], int), f"meta.armed must be int, got {type(meta['armed'])}"


def test_api_state_meta_triggered_is_int(client):
    """meta.triggered must be an integer count of triggered symphonies."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "triggered" not in meta:
        pytest.fail("meta.triggered missing from /api/state response")
    assert isinstance(meta["triggered"], int), (
        f"meta.triggered must be int, got {type(meta['triggered'])}"
    )


def test_api_state_meta_system_online_is_bool(client):
    """meta.system_online must be a boolean."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "system_online" not in meta:
        pytest.fail("meta.system_online missing from /api/state response")
    assert isinstance(meta["system_online"], bool), (
        f"meta.system_online must be bool, got {type(meta['system_online'])}"
    )


# ---------------------------------------------------------------------------
# AC-C1.5 — meta.mode reflects live_mode flag
# ---------------------------------------------------------------------------


def test_api_state_meta_mode_dry_run_when_live_false(client):
    """meta.mode must be 'DRY RUN' when LIVE_EXECUTION=False."""
    env_stub = {"LIVE_EXECUTION": "False", "EXECUTION_START_TIME": "09:30"}
    with (
        patch("database.load_state", return_value={}),
        patch("dotenv.dotenv_values", return_value=env_stub),
    ):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "mode" not in meta:
        pytest.fail("meta.mode missing from /api/state response")
    assert meta["mode"] == "DRY RUN", (
        f"Expected meta.mode='DRY RUN', got '{meta['mode']}'. "
        "LIVE_EXECUTION=False must produce mode='DRY RUN'."
    )


def test_api_state_meta_mode_live_when_live_true(client):
    """meta.mode must be 'LIVE' when LIVE_EXECUTION=True."""
    env_stub = {"LIVE_EXECUTION": "True", "EXECUTION_START_TIME": "09:30"}
    with (
        patch("database.load_state", return_value={}),
        patch("dotenv.dotenv_values", return_value=env_stub),
    ):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "mode" not in meta:
        pytest.fail("meta.mode missing from /api/state response")
    assert meta["mode"] == "LIVE", (
        f"Expected meta.mode='LIVE', got '{meta['mode']}'. "
        "LIVE_EXECUTION=True must produce mode='LIVE'."
    )


# ---------------------------------------------------------------------------
# AC-C1.6 — meta.next_run is "mm:ss" formatted string
# ---------------------------------------------------------------------------


def test_api_state_meta_next_run_is_mmss_string(client):
    """meta.next_run must be a 'mm:ss' formatted string."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "next_run" not in meta:
        pytest.fail("meta.next_run missing from /api/state response")
    next_run = meta["next_run"]
    assert isinstance(next_run, str), f"meta.next_run must be a string, got {type(next_run)}"
    assert re.match(r"^\d{2}:\d{2}$", next_run), (
        f"meta.next_run must match mm:ss format, got '{next_run}'. "
        "Derive from next_run_seconds: f'{seconds // 60:02d}:{seconds % 60:02d}'."
    )


# ---------------------------------------------------------------------------
# AC-C1.7 — Nav contains links to all 5 Studio routes
# ---------------------------------------------------------------------------

STUDIO_ROUTES = [
    ("/", "dashboard"),
    ("/performance", "performance"),
    ("/ai-advisor", "advisor"),
    ("/history", "history"),
    ("/settings", "settings"),
]


@pytest.mark.parametrize("target_href,label_hint", STUDIO_ROUTES)
@pytest.mark.parametrize("page_route", ["/", "/performance", "/ai-advisor"])
def test_nav_contains_link_to_each_studio_route(client, page_route, target_href, label_hint):
    """Every rendered page must have a nav link to each Studio route.

    The chrome include must render all 5 nav links regardless of current page.
    """
    resp = client.get(page_route)
    html = resp.data.decode("utf-8")
    assert f'href="{target_href}"' in html or f"href='{target_href}'" in html, (
        f"Page {page_route}: missing nav link to '{target_href}' ({label_hint}). "
        "The studio chrome nav must include all 5 routes."
    )


# ---------------------------------------------------------------------------
# AC-C1.8 — Active route tab has aria-current="page" or data-active="true"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page_route,active_href",
    [
        ("/", "/"),
        ("/performance", "/performance"),
        ("/ai-advisor", "/ai-advisor"),
    ],
)
def test_active_nav_tab_has_aria_current(client, page_route, active_href):
    """Active nav tab must have aria-current='page' on the link/button for the current route."""
    resp = client.get(page_route)
    html = resp.data.decode("utf-8")
    # We accept either aria-current="page" on the anchor or data-active="true"
    has_aria_current = 'aria-current="page"' in html or "aria-current='page'" in html
    has_data_active = 'data-active="true"' in html or "data-active='true'" in html
    assert has_aria_current or has_data_active, (
        f"Page {page_route}: active nav tab missing aria-current='page' or data-active='true'. "
        "The Studio chrome must mark the active route tab for accessibility."
    )


# ---------------------------------------------------------------------------
# AC-C1.9 — Tweaks button present with data-testid="tweaks-btn"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page_route", ["/", "/performance", "/ai-advisor"])
def test_tweaks_button_present(client, page_route):
    """Every Studio page must render the tweaks trigger button."""
    resp = client.get(page_route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="tweaks-btn"' in html, (
        f"Page {page_route}: missing tweaks trigger button (data-testid='tweaks-btn'). "
        "The Studio chrome must include the tweaks panel trigger."
    )


# ---------------------------------------------------------------------------
# AC-C1.10 — No hardcoded hex in rendered template HTML (sampling check)
# ---------------------------------------------------------------------------

_INLINE_HEX_PATTERN = re.compile(r'(?:style=["\'][^"\']*|color=)#[0-9a-fA-F]{3,8}\b')
# Tokens.css is the only permitted source; inline style hex in HTML is a violation.


def test_dashboard_no_inline_hex_colors(client):
    """Rendered dashboard HTML must not contain inline hex color values.

    All colors must be driven by --studio-* CSS variables. Any inline hex in
    a `style` attribute signals a token-system bypass.

    This is a sampling check — it covers the main dashboard page.
    """
    resp = client.get("/")
    html = resp.data.decode("utf-8")
    matches = _INLINE_HEX_PATTERN.findall(html)
    assert not matches, (
        f"Dashboard HTML contains inline hex colors (style='color: #...'): {matches[:5]}. "
        "All colors must use --studio-* CSS variables from tokens.css."
    )


# ---------------------------------------------------------------------------
# AC-C1.11 — _chrome.html include file exists
# ---------------------------------------------------------------------------


def test_chrome_html_include_exists():
    """templates/_chrome.html must exist as the shared nav include."""
    chrome_path = PROJECT_ROOT / "templates" / "_chrome.html"
    assert chrome_path.exists(), (
        "templates/_chrome.html not found. "
        "Implementer must create this Jinja include for the Studio chrome nav."
    )


# ---------------------------------------------------------------------------
# AC-C1.12 — tokens.css is linked from every existing template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page_route", ["/", "/performance", "/ai-advisor"])
def test_tokens_css_linked_in_templates(client, page_route):
    """Every Studio page must link tokens.css."""
    resp = client.get(page_route)
    html = resp.data.decode("utf-8")
    assert "tokens.css" in html, (
        f"Page {page_route}: missing <link> to tokens.css. "
        "Every template must load the CSS variable token sheet."
    )


# ---------------------------------------------------------------------------
# AC-C1.13 — tweaks.js is loaded from every existing template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page_route", ["/", "/performance", "/ai-advisor"])
def test_tweaks_js_loaded_in_templates(client, page_route):
    """Every Studio page must load tweaks.js."""
    resp = client.get(page_route)
    html = resp.data.decode("utf-8")
    assert "tweaks.js" in html, (
        f"Page {page_route}: missing <script> for tweaks.js. "
        "Every template must load the tweaks persistence layer."
    )


# ---------------------------------------------------------------------------
# AC-C1.14 — meta.account present with label and uuid_short
# ---------------------------------------------------------------------------


def test_api_state_meta_account_present(client):
    """meta.account must be present with label and uuid_short sub-keys."""
    env_stub = {
        "LIVE_EXECUTION": "False",
        "EXECUTION_START_TIME": "09:30",
        "ACCOUNT_ROTH": "880be47e-0000-0000-0000-000000000000",
    }
    with (
        patch("database.load_state", return_value={}),
        patch("dotenv.dotenv_values", return_value=env_stub),
    ):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    assert "account" in meta, (
        "meta.account missing from /api/state. "
        "Must contain {label, uuid_short} for the workspace switcher."
    )
    account = meta["account"]
    assert "label" in account, "meta.account.label missing"
    assert "uuid_short" in account, "meta.account.uuid_short missing"
    assert isinstance(account["label"], str) and account["label"], (
        "meta.account.label must be non-empty string"
    )
    uuid_short = account["uuid_short"]
    assert isinstance(uuid_short, str), "meta.account.uuid_short must be a string"
    assert len(uuid_short) <= 8, (
        f"meta.account.uuid_short must be ≤8 chars for compact display, got '{uuid_short}' ({len(uuid_short)} chars)"
    )


# ---------------------------------------------------------------------------
# AC-C1.15 — meta.tracked counts only symphony entries (not metadata keys)
# ---------------------------------------------------------------------------


def test_api_state_meta_tracked_counts_symphony_entries(client):
    """meta.tracked must equal the number of dict entries with a 'name' key in state."""
    state_stub = {
        "sym_a": {
            "name": "Symphony A",
            "account": "acc1",
            "current_return": 0.5,
            "current_value": 1000.0,
        },
        "sym_b": {
            "name": "Symphony B",
            "account": "acc1",
            "current_return": 1.2,
            "current_value": 2000.0,
        },
        "last_successful_cycle_at": "2026-05-18T09:30:00",  # non-symphony key
    }
    with patch("database.load_state", return_value=state_stub):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "tracked" not in meta:
        pytest.fail("meta.tracked missing")
    assert meta["tracked"] == 2, (
        f"meta.tracked should be 2 (only count dict entries with 'name' key), got {meta['tracked']}. "
        "Non-symphony metadata keys like 'last_successful_cycle_at' must not be counted."
    )


def test_api_state_meta_armed_counts_armed_and_tp_and_para(client):
    """meta.armed must count symphonies where armed=True OR tp_armed=True OR para_armed=True."""
    state_stub = {
        "sym_a": {
            "name": "A",
            "account": "acc1",
            "current_return": 0.0,
            "current_value": 1000.0,
            "armed": True,
        },
        "sym_b": {
            "name": "B",
            "account": "acc1",
            "current_return": 0.0,
            "current_value": 1000.0,
            "tp_armed": True,
        },
        "sym_c": {
            "name": "C",
            "account": "acc1",
            "current_return": 0.0,
            "current_value": 1000.0,
            "para_armed": True,
        },
        "sym_d": {
            "name": "D",
            "account": "acc1",
            "current_return": 0.0,
            "current_value": 1000.0,
        },  # standby
    }
    with patch("database.load_state", return_value=state_stub):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "armed" not in meta:
        pytest.fail("meta.armed missing")
    assert meta["armed"] == 3, (
        f"meta.armed should be 3 (armed + tp_armed + para_armed each count), got {meta['armed']}. "
        "All three arming states contribute to the armed count."
    )


def test_api_state_meta_triggered_counts_only_triggered_symphonies(client):
    """meta.triggered must count only symphonies with triggered=True."""
    state_stub = {
        "sym_a": {
            "name": "A",
            "account": "acc1",
            "current_return": 1.5,
            "current_value": 1000.0,
            "triggered": True,
        },
        "sym_b": {
            "name": "B",
            "account": "acc1",
            "current_return": 0.5,
            "current_value": 1000.0,
            "triggered": True,
        },
        "sym_c": {
            "name": "C",
            "account": "acc1",
            "current_return": 0.2,
            "current_value": 1000.0,
        },  # not triggered
    }
    with patch("database.load_state", return_value=state_stub):
        resp = client.get("/api/state")
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    if "triggered" not in meta:
        pytest.fail("meta.triggered missing")
    assert meta["triggered"] == 2, (
        f"meta.triggered should be 2, got {meta['triggered']}. "
        "Only symphonies with triggered=True should be counted."
    )


# ---------------------------------------------------------------------------
# AC-C1.16 — Empty state still produces valid meta object
# ---------------------------------------------------------------------------


def test_api_state_meta_valid_with_empty_bot_state(client):
    """meta must be present and valid even when bot_state is empty (fresh deploy)."""
    with patch("database.load_state", return_value={}):
        resp = client.get("/api/state")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "meta" in data, "meta must be present even with empty bot_state"
    meta = data["meta"]
    assert meta.get("tracked", -1) == 0, (
        f"meta.tracked should be 0 for empty state, got {meta.get('tracked')}"
    )
    assert meta.get("armed", -1) == 0, (
        f"meta.armed should be 0 for empty state, got {meta.get('armed')}"
    )
    assert meta.get("triggered", -1) == 0, (
        f"meta.triggered should be 0 for empty state, got {meta.get('triggered')}"
    )
