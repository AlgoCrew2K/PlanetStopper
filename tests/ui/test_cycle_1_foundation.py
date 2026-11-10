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
        and not line.strip().endswith("*/")  # end of block comment
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
        "ACCOUNT_ROTH": "ACCOUNT_ID_REDACTED",
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


# ---------------------------------------------------------------------------
# App shell: the shared header wraps at narrow widths, its fonts come from one partial,
# and the mode pill reads the same on every page.
#
# Layout facts (a control inside the viewport) are asserted on a live render in
# tests/ui/test_shell_header_browser.py; this section pins what the server renders: the
# header's layout lives in classes a narrow-width rule can override (an inline style cannot be
# overridden by a media rule), the wrap rule sits inside the existing 1400 px breakpoint, and
# the font links moved to one partial that renders the same bytes at the same place.
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402
from html.parser import HTMLParser  # noqa: E402

_SHELL_PAGES = ["/", "/history", "/performance", "/settings", "/ai-advisor"]

# sha256 of the three font lines (two preconnect hints and the stylesheet link) that began
# templates/_chrome.html at the plan base, joined by LF without a trailing newline.
_BASE_FONT_LINES_SHA256 = "648e40b6981b508f6667b4fc0837a3c5b7029e0803584da7768f469858da7375"

_LAYOUT_PROPERTIES = {
    "display",
    "flex",
    "flex-wrap",
    "flex-shrink",
    "gap",
    "margin",
    "margin-left",
    "min-width",
    "overflow",
    "align-items",
}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}


class _ChromeNodes(HTMLParser):
    """The attributes of the header's nav, its link group and its status cluster."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict]] = []
        self.nav: dict | None = None
        self.links: dict | None = None
        self.status: dict | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "nav" and attr.get("data-testid") == "studio-nav":
            self.nav = attr
        if (
            tag == "a"
            and self.links is None
            and len(self.stack) >= 2
            and self.stack[-1][0] == "div"
            and self.stack[-2][0] == "nav"
        ):
            self.links = self.stack[-1][1]
        if attr.get("id") == "engine-status" and self.stack and self.stack[-1][0] == "div":
            self.status = self.stack[-1][1]
        if tag not in _VOID_TAGS:
            self.stack.append((tag, attr))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def _chrome_nodes(page: str) -> _ChromeNodes:
    parser = _ChromeNodes()
    parser.feed(page)
    parser.close()
    assert parser.nav is not None, "the page renders no studio nav"
    assert parser.links is not None, "the nav has no link group"
    assert parser.status is not None, "the nav has no status cluster"
    return parser


def _inline_properties(attrs: dict) -> set[str]:
    style = attrs.get("style") or ""
    return {decl.split(":", 1)[0].strip().lower() for decl in style.split(";") if ":" in decl}


def _chrome_template_text() -> str:
    return (PROJECT_ROOT / "templates" / "_chrome.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("route", _SHELL_PAGES)
@pytest.mark.parametrize("element", ["nav", "links", "status"])
def test_the_header_layout_is_in_a_class_not_an_inline_style(client, route, element):
    """a media rule can wrap the header only if its layout is not inline."""
    nodes = _chrome_nodes(client.get(route).get_data(as_text=True))
    attrs = getattr(nodes, element)
    leaked = _inline_properties(attrs) & _LAYOUT_PROPERTIES
    assert not leaked, f"{element} keeps layout properties inline: {sorted(leaked)}"
    assert (attrs.get("class") or "").strip(), f"{element} carries no class for its layout"


def _rules_at_1400(page: str):
    from tests.ui import _dashboard_primitives_capture as cap

    for block in re.findall(r"<style\b[^>]*>(.*?)</style>", page, flags=re.DOTALL):
        for context, selectors, decls in cap.iter_rules(block):
            if re.search(r"max-width:\s*1400px", context):
                yield selectors, dict(decls)


@pytest.mark.parametrize("element", ["nav", "links"])
def test_the_nav_and_its_link_group_wrap_inside_the_existing_1400px_breakpoint(client, element):
    """wrap, not collapse, and at the breakpoint the header already has."""
    page = client.get("/history").get_data(as_text=True)
    attrs = getattr(_chrome_nodes(page), element)
    classes = (attrs.get("class") or "").split()
    wanted = {f".{c}" for c in classes}
    if element == "nav":
        wanted |= {'[data-testid="studio-nav"]', "nav"}
    matches = [
        decls
        for selectors, decls in _rules_at_1400(page)
        if any(sel.split()[-1].split(":")[0] in wanted for sel in selectors)
    ]
    assert any(d.get("flex-wrap") == "wrap" for d in matches), (
        f"no rule inside @media (max-width: 1400px) sets flex-wrap: wrap on the {element}"
    )


def test_the_header_introduces_no_new_breakpoint_value():
    """/ overview: the wrap uses the breakpoint the nav already has."""
    widths = set(re.findall(r"@media\s*\(max-width:\s*(\d+)px\)", _chrome_template_text()))
    assert widths <= {"1400", "700"}, sorted(widths)


def test_the_font_links_live_in_one_partial_the_chrome_includes():
    """login and the chrome share one font source."""
    partial = PROJECT_ROOT / "templates" / "_fonts.html"
    assert partial.is_file(), "templates/_fonts.html does not exist"
    text = partial.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _BASE_FONT_LINES_SHA256, (
        "the partial must hold the three font lines of the base chrome verbatim"
    )
    chrome = _chrome_template_text()
    assert "fonts.googleapis.com" not in chrome, "the chrome still carries its own font links"
    assert re.search(r"\{%\s*include\s+['\"]_fonts\.html['\"]\s*%\}", chrome)


@pytest.mark.parametrize("route", _SHELL_PAGES)
def test_the_rendered_font_lines_are_the_base_bytes_at_the_same_place(client, route):
    """the same rendered bytes before the header as at the base."""
    page = client.get(route).get_data(as_text=True).replace("\r\n", "\n")
    marker = '<div data-testid="top-bar"'
    assert marker in page
    before = page[: page.index(marker)].rstrip("\n")
    # the include keeps the page's indentation on its first line only; the lines are compared bare
    last_three = "\n".join(line.strip() for line in before.split("\n")[-3:])
    assert hashlib.sha256(last_three.encode("utf-8")).hexdigest() == _BASE_FONT_LINES_SHA256


@pytest.mark.parametrize("route", _SHELL_PAGES)
def test_every_header_control_is_still_rendered_on_every_page(client, route):
    """Render half: the indicator and its badge, and the three controls."""
    page = client.get(route).get_data(as_text=True)
    for marker in (
        'data-testid="candidate-alert-indicator"',
        'data-testid="candidate-alert-badge"',
        'data-testid="mode-pill"',
        'data-testid="force-run-btn"',
        'id="emergency-liquidate"',
        'data-testid="engine-status"',
    ):
        assert marker in page, f"{route} lost {marker}"


@pytest.mark.parametrize("live, text", [(True, "LIVE"), (False, "DRY RUN")])
@pytest.mark.parametrize("route", _SHELL_PAGES)
def test_the_mode_pill_reads_live_or_dry_run_on_every_page(client, route, live, text):
    """the pill's text and state follow the live flag, on every page."""
    import app as app_module

    env = {"LIVE_EXECUTION": "True" if live else "False", "EXECUTION_START_TIME": "09:30"}
    with (
        patch("database.load_state", return_value={}),
        patch("dotenv.dotenv_values", return_value=env),
        patch.object(app_module, "dotenv_values", lambda *_a, **_k: dict(env)),
    ):
        page = client.get(route).get_data(as_text=True)
    # The pill carries a definition `title`, so the attributes are matched in any order.
    pill = re.search(
        r'data-testid="mode-pill"([^>]*?)data-live="(\w+)"[^>]*>\s*([A-Z ]+?)\s*<', page
    )
    assert pill, f"{route} renders no mode pill"
    assert pill.group(2) == ("true" if live else "false")
    assert pill.group(3) == text
    if live:
        opening_tag = page[page.index('data-testid="mode-pill"') :].split(">", 1)[0]
        assert "var(--studio-neg)" in opening_tag
