"""
Cycle 1 — UX BLOCK tests (RED).

Source: docs/handoff/cycle-1-ux-review.md (229a050 review, CHANGES REQUESTED)
Encodes 4 BLOCKs flagged by ux (flask-dashboard-specialist):

  BLOCK-1  Top-bar right-rail elements missing:
             - system-online dot + ONLINE·NEXT ticker
             - workspace switcher chip (label + uuid_short + ▾)
             - LIVE/DRY RUN mode pill with data-live attribute
             - Force run now button (data-testid="force-run-btn")

  BLOCK-2  tokens.css missing:
             - [data-theme="dark"] override block with dark palette vars
             - --studio-pos-dim and --studio-neg-dim in :root

  BLOCK-3  Tweaks panel missing:
             - Accent colour swatch controls (data-testid="accent-swatch")
             - Typeface selector

  BLOCK-4  tweaks.js does not apply stored values to DOM:
             - No applyAll() on DOMContentLoaded
             - No tweakchange listener that propagates changes live
             - No data-theme / data-density attribute application
             - No --studio-accent / --studio-sans CSS var application
"""

from __future__ import annotations

import pathlib
import re

import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# BLOCK-1 — Top-bar right-rail elements
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor", "/history", "/settings"])
def test_topbar_engine_status_indicator_present(client, route):
    """Top-bar must render the Live/Stale/Closed engine-status indicator on all routes.

    The old system-online-dot was replaced by #engine-status (wrapper) +
    #engine-status-dot (color dot) + #engine-status-label (Live/Stale/Closed text)
    in templates/_chrome.html (commits 4b6d8c3 / 45a6000).
    All three elements must be present in the chrome included on every route.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="engine-status"' in html, (
        f"Page {route}: missing engine-status indicator (data-testid='engine-status'). "
        "Chrome topbar must include the Live/Stale/Closed status wrapper from _chrome.html."
    )
    assert 'id="engine-status-dot"' in html, (
        f"Page {route}: missing #engine-status-dot. "
        "The status dot must be present as a child of #engine-status in _chrome.html."
    )
    assert 'id="engine-status-label"' in html, (
        f"Page {route}: missing #engine-status-label. "
        "The Live/Stale/Closed label must be present as a child of #engine-status in _chrome.html."
    )


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor", "/history", "/settings"])
def test_topbar_next_run_countdown_present(client, route):
    """Top-bar must render the NEXT MM:SS countdown element on all routes.

    The old next-run-ticker was replaced by #next-run-countdown in _chrome.html
    (commits 4b6d8c3 / 45a6000). The element is present in the DOM on all routes
    (hidden via inline style when market is closed; JS ticks it every second when open).
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="next-run-countdown"' in html, (
        f"Page {route}: missing next-run-countdown element (data-testid='next-run-countdown'). "
        "Chrome topbar must include the NEXT MM:SS countdown from _chrome.html."
    )


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_topbar_workspace_switcher_present(client, route):
    """Top-bar must render the workspace switcher chip (account label + uuid_short).

    Design (chrome.jsx lines 130-148): button chip showing account.label, account.uuid_short,
    and a ▾ chevron. Drives the workspace switcher dropdown.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="workspace-switcher"' in html, (
        f"Page {route}: missing workspace switcher chip (data-testid='workspace-switcher'). "
        "Top-bar must include account label + uuid_short chip per chrome.jsx lines 130-148."
    )


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_topbar_mode_pill_present(client, route):
    """Top-bar must render the LIVE/DRY RUN mode pill.

    Design (chrome.jsx lines 150-166): pill with data-live attribute set to 'true'
    when mode is LIVE (triggers danger CSS), 'false' for DRY RUN.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="mode-pill"' in html, (
        f"Page {route}: missing LIVE/DRY RUN mode pill (data-testid='mode-pill'). "
        "Top-bar must include the mode indicator from chrome.jsx lines 150-166."
    )


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_topbar_force_run_button_present(client, route):
    """Top-bar must render the Force run now button.

    Design (chrome.jsx lines 168-183): accent-background button that POSTs to /api/trigger.
    Must carry data-testid='force-run-btn' and must NOT use inline onclick.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="force-run-btn"' in html, (
        f"Page {route}: missing Force run now button (data-testid='force-run-btn'). "
        "Top-bar must include the force-run action button from chrome.jsx lines 168-183."
    )


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_topbar_force_run_button_no_inline_onclick(client, route):
    """Force run now button must NOT use inline onclick attribute.

    Per ux prime directive: non-trivial JS belongs in static/ files, not inline HTML.
    The click handler must be wired via an event listener in tweaks.js or chrome.js.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    # Find the force-run button and check it has no inline onclick
    # We look for onclick near force-run-btn in a 200-char window
    idx = html.find('data-testid="force-run-btn"')
    if idx == -1:
        pytest.skip("force-run-btn not present yet (covered by separate test)")
    surrounding = html[max(0, idx - 100): idx + 200]
    assert "onclick" not in surrounding, (
        "Force run now button must not use inline onclick. "
        "Wire click handler via addEventListener in static/tweaks.js or static/chrome.js."
    )


def test_topbar_mode_pill_has_data_live_attribute(client):
    """Mode pill must have a data-live attribute set to 'true' when mode is LIVE.

    This attribute drives CSS danger styling via [data-live='true'] selector in tokens.css.
    """
    env_stub = {"LIVE_EXECUTION": "True", "EXECUTION_START_TIME": "09:30"}
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "database.load_state", return_value={}
    ), __import__("unittest.mock", fromlist=["patch"]).patch(
        "dotenv.dotenv_values", return_value=env_stub
    ):
        resp = client.get("/")
    html = resp.data.decode("utf-8")
    idx = html.find('data-testid="mode-pill"')
    if idx == -1:
        pytest.fail("mode-pill not present (covered by separate test)")
    surrounding = html[max(0, idx - 20): idx + 150]
    assert 'data-live="true"' in surrounding, (
        "Mode pill must have data-live='true' when LIVE_EXECUTION=True. "
        "This attribute drives danger CSS styling in tokens.css."
    )


# ---------------------------------------------------------------------------
# BLOCK-2 — tokens.css dark theme + missing vars
# ---------------------------------------------------------------------------


def test_tokens_css_has_dark_theme_block():
    """tokens.css must contain a [data-theme='dark'] override block.

    Design (studio.jsx lines 10-26): full dark palette. When tweaks.js sets
    data-theme='dark' on <html>, CSS vars must flip to dark values.
    Without this block, the theme toggle is broken.
    """
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    assert '[data-theme="dark"]' in content or "[data-theme='dark']" in content, (
        "tokens.css missing [data-theme='dark'] override block. "
        "Add a full dark-palette block matching studioPalette() dark output from studio.jsx lines 10-26."
    )


def test_tokens_css_dark_block_has_bg_var():
    """tokens.css dark block must declare --studio-bg with dark value #13100c."""
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    if "[data-theme" not in content:
        pytest.fail("No [data-theme] block in tokens.css")
    dark_idx = content.find("[data-theme")
    dark_block = content[dark_idx:]
    assert "--studio-bg" in dark_block, (
        "tokens.css [data-theme='dark'] block missing --studio-bg. "
        "Dark value should be #13100c per studio.jsx."
    )


def test_tokens_css_dark_block_has_paper_var():
    """tokens.css dark block must declare --studio-paper."""
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    if "[data-theme" not in content:
        pytest.fail("No [data-theme] block in tokens.css")
    dark_idx = content.find("[data-theme")
    dark_block = content[dark_idx:]
    assert "--studio-paper" in dark_block, (
        "tokens.css [data-theme='dark'] block missing --studio-paper."
    )


def test_tokens_css_dark_block_has_ink_var():
    """tokens.css dark block must declare --studio-ink."""
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    if "[data-theme" not in content:
        pytest.fail("No [data-theme] block in tokens.css")
    dark_idx = content.find("[data-theme")
    dark_block = content[dark_idx:]
    assert "--studio-ink" in dark_block, (
        "tokens.css [data-theme='dark'] block missing --studio-ink."
    )


def test_tokens_css_has_pos_dim_var():
    """tokens.css :root must declare --studio-pos-dim.

    Used by card chip backgrounds and opacity layers in studio.jsx.
    Light value: rgba(31,122,77,0.55) per studioPalette().
    """
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    assert "--studio-pos-dim" in content, (
        "tokens.css missing --studio-pos-dim. "
        "Add to :root block: rgba(31,122,77,0.55) for light theme."
    )


def test_tokens_css_has_neg_dim_var():
    """tokens.css :root must declare --studio-neg-dim.

    Used by negative-state chip backgrounds in studio.jsx.
    Light value: rgba(180,58,42,0.55) per studioPalette().
    """
    tokens_path = PROJECT_ROOT / "static" / "tokens.css"
    if not tokens_path.exists():
        pytest.skip("tokens.css does not exist yet")
    content = tokens_path.read_text(encoding="utf-8")
    assert "--studio-neg-dim" in content, (
        "tokens.css missing --studio-neg-dim. "
        "Add to :root block: rgba(180,58,42,0.55) for light theme."
    )


# ---------------------------------------------------------------------------
# BLOCK-3 — Tweaks panel missing accent swatch + typeface selector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_tweaks_panel_has_accent_swatches(client, route):
    """Tweaks panel must render accent colour swatch buttons.

    Design (index.html lines 197-204, TweakColor in tweaks-panel.jsx):
    5 accent swatches (#1f7a4d, #0b6cc4, #b04420, #6a3f8a, #3a3a3a) each with
    data-testid='accent-swatch' and aria-checked reflecting active swatch.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="accent-swatch"' in html, (
        f"Page {route}: tweaks panel missing accent colour swatches (data-testid='accent-swatch'). "
        "Add 5 swatch buttons per TweakColor pattern in tweaks-panel.jsx."
    )


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_tweaks_panel_has_five_accent_swatches(client, route):
    """Tweaks panel must render exactly 5 accent swatch buttons."""
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    count = html.count('data-testid="accent-swatch"')
    assert count == 5, (
        f"Page {route}: expected 5 accent swatches, found {count}. "
        "Swatches: #1f7a4d, #0b6cc4, #b04420, #6a3f8a, #3a3a3a per index.html line 58."
    )


@pytest.mark.parametrize("route", ["/", "/performance", "/ai-advisor"])
def test_tweaks_panel_has_typeface_selector(client, route):
    """Tweaks panel must render a typeface selector.

    Design (index.html lines 205-209, TweakSelect): select or radio with options
    Manrope / Geist / DM Sans / Plus Jakarta Sans / IBM Plex Sans.
    Must carry data-testid='typeface-select'.
    """
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert 'data-testid="typeface-select"' in html, (
        f"Page {route}: tweaks panel missing typeface selector (data-testid='typeface-select'). "
        "Add typeface <select> with 5 font options per index.html lines 205-209."
    )


@pytest.mark.parametrize("typeface", [
    "Manrope", "Geist", "DM Sans", "Plus Jakarta Sans", "IBM Plex Sans"
])
@pytest.mark.parametrize("route", ["/"])
def test_tweaks_panel_typeface_selector_has_all_options(client, route, typeface):
    """Typeface selector must include all 5 font options from the design."""
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    assert typeface in html, (
        f"Page {route}: typeface option '{typeface}' missing from tweaks panel. "
        "All 5 typefaces must be present: Manrope, Geist, DM Sans, Plus Jakarta Sans, IBM Plex Sans."
    )


# ---------------------------------------------------------------------------
# BLOCK-4 — tweaks.js DOM application on load + live update
# ---------------------------------------------------------------------------


def test_tweaks_js_applies_theme_on_load():
    """tweaks.js must apply stored theme to document on DOMContentLoaded.

    Without this, a user who sets dark theme and reloads gets the light theme back.
    Required: document.documentElement.setAttribute('data-theme', ...) called on load.
    """
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "data-theme" in content, (
        "tweaks.js must apply data-theme attribute to document.documentElement on load. "
        "Call document.documentElement.setAttribute('data-theme', values.theme) in applyAll()."
    )


def test_tweaks_js_applies_density_on_load():
    """tweaks.js must apply stored density to document on DOMContentLoaded."""
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "data-density" in content, (
        "tweaks.js must apply data-density attribute to document.documentElement on load. "
        "Call document.documentElement.setAttribute('data-density', values.density) in applyAll()."
    )


def test_tweaks_js_applies_accent_css_var_on_load():
    """tweaks.js must apply stored accent as --studio-accent CSS var on load."""
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "--studio-accent" in content, (
        "tweaks.js must apply --studio-accent CSS var on load. "
        "Call document.documentElement.style.setProperty('--studio-accent', values.accent)."
    )


def test_tweaks_js_applies_typeface_css_var_on_load():
    """tweaks.js must apply stored typeface as --studio-sans CSS var on load."""
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "--studio-sans" in content, (
        "tweaks.js must apply --studio-sans CSS var on load. "
        "Call document.documentElement.style.setProperty('--studio-sans', values.typeface + ...)."
    )


def test_tweaks_js_listens_to_tweakchange_event():
    """tweaks.js must listen to the tweakchange CustomEvent and apply changes live.

    Design pattern (tweaks-panel.jsx setTweak): dispatches 'tweakchange' CustomEvent.
    tweaks.js must addEventListener('tweakchange', ...) and call applyAll() in response.
    Without this, live-update (AC-S.2: 'Changes apply live without reload') is broken.
    """
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "tweakchange" in content, (
        "tweaks.js must listen to 'tweakchange' CustomEvent for live updates. "
        "Add: window.addEventListener('tweakchange', () => applyAll(getAll()))."
    )


def test_tweaks_js_calls_apply_on_domcontentloaded():
    """tweaks.js must apply stored values on DOMContentLoaded (not just seed them).

    Seeding localStorage keys without applying them to the DOM means the page
    renders with default CSS vars regardless of what the user previously set.
    """
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "DOMContentLoaded" in content, (
        "tweaks.js must register a DOMContentLoaded listener to apply stored tweaks on page load. "
        "Add: document.addEventListener('DOMContentLoaded', () => applyAll(getAll()))."
    )


# ---------------------------------------------------------------------------
# NIT-3 encoded as a test — density value "roomy" (not "comfortable")
# NIT is borderline functional: localStorage key 'roomy' must match CSS [data-density='roomy']
# ---------------------------------------------------------------------------


def test_tweaks_js_uses_roomy_not_comfortable():
    """tweaks.js density default must use 'roomy' not 'comfortable'.

    Design (index.html TWEAK_DEFAULTS, chrome.jsx density options): values are
    'compact', 'balanced', 'roomy'. If tweaks.js seeds 'comfortable', the stored value
    will never match a CSS [data-density='roomy'] selector.
    """
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "roomy" in content, (
        "tweaks.js density options must use 'roomy' (not 'comfortable'). "
        "Matches design TWEAK_DEFAULTS and CSS [data-density='roomy'] selector."
    )
    assert "comfortable" not in content, (
        "tweaks.js must not use 'comfortable' as a density value — use 'roomy' instead."
    )


@pytest.mark.parametrize("route", ["/"])
def test_chrome_density_option_roomy_not_comfortable(client, route):
    """Rendered HTML tweaks panel density options must include 'roomy' not 'comfortable'."""
    resp = client.get(route)
    html = resp.data.decode("utf-8")
    # Only assert if a density control is present at all
    if "density" not in html.lower():
        pytest.skip("No density control rendered yet")
    assert "roomy" in html, (
        "Tweaks panel density option must be 'roomy', not 'comfortable'. "
        "Matches TWEAK_DEFAULTS in index.html line 52."
    )


# ---------------------------------------------------------------------------
# UX v2 finding — density DEFAULT must be 'balanced', not 'roomy'
# The NIT-3 fix (comfortable → roomy) corrected the option label, but the
# TWEAK_DEFAULTS seed value must remain 'balanced' per design spec
# (.design-handoff/project/index.html TWEAK_DEFAULTS + cycle-1-contract.md).
# ---------------------------------------------------------------------------


def test_tweaks_js_density_default_is_balanced():
    """TWEAK_DEFAULTS.density in tweaks.js must be 'balanced', not 'roomy'.

    Design spec (.design-handoff/project/index.html TWEAK_DEFAULTS):
        density: 'balanced'
    The NIT-3 fix renamed the option value 'comfortable' → 'roomy' (correct),
    but must not change the default seed from 'balanced' to 'roomy'.
    A new user who has never set a density preference should get 'balanced'.
    """
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    # The TWEAK_DEFAULTS object must seed density as 'balanced'
    assert re.search(r"density\s*:\s*['\"]balanced['\"]", content), (
        "tweaks.js TWEAK_DEFAULTS.density must be 'balanced' (design default). "
        "The 'roomy' option value is a valid label, but the seed default is 'balanced'."
    )


# ---------------------------------------------------------------------------
# rev BLOCK tests — token hygiene (cycle-1-review.md @ 8f1d5a1)
# All bare hex outside tokens.css is a hard constraint violation.
# ---------------------------------------------------------------------------


def test_chrome_html_no_bare_hex():
    """_chrome.html must contain no bare hex colour literals.

    rev BLOCK: two violations found at HEAD 9ce1e7d:
      - line 59: color:#fff on the mode-pill inline style
      - lines 100-112: data-color="#1f7a4d" etc. on accent-swatch buttons

    Correct fix:
      - Replace #fff with var(--studio-white) in the inline style.
      - Replace data-color="#hexval" with data-swatch-var="--studio-swatch-N"
        and resolve the value in JS via getComputedStyle at click time.
    """
    chrome_path = PROJECT_ROOT / "templates" / "_chrome.html"
    if not chrome_path.exists():
        pytest.skip("_chrome.html does not exist yet")
    content = chrome_path.read_text(encoding="utf-8")
    # Strip Jinja block tags so we don't accidentally match template syntax
    # Strip HTML comments
    import re as _re
    stripped = _re.sub(r"\{#.*?#\}", "", content)
    bare_hex = _re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert bare_hex == [], (
        f"_chrome.html contains bare hex literals (token hygiene violation): {bare_hex}. "
        "All colours must use var(--studio-*) tokens. "
        "Replace #fff with var(--studio-white); replace data-color hex with data-swatch-var CSS-var refs."
    )


def test_tweaks_css_no_bare_hex():
    """tweaks.css must contain no bare hex colour literals outside var() fallbacks.

    rev BLOCK: tweaks.css line 153 uses `background: #fff` — bare hex on toggle thumb.
    Correct fix: replace with `background: var(--studio-white)`.

    Note: var(--studio-foo, #hexval) fallbacks in tweaks.css are also flagged as NITs
    (duplication of tokens.css values) but only the non-fallback bare hex is a BLOCK.
    This test targets non-fallback bare hex (i.e., hex not inside a var() expression).
    """
    tweaks_css_path = PROJECT_ROOT / "static" / "tweaks.css"
    if not tweaks_css_path.exists():
        pytest.skip("tweaks.css does not exist yet")
    content = tweaks_css_path.read_text(encoding="utf-8")
    import re as _re
    # Strip var(...) expressions so fallback hex inside var() is not flagged
    stripped = _re.sub(r"var\([^)]*\)", "", content)
    bare_hex = _re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert bare_hex == [], (
        f"tweaks.css contains bare hex outside var() expressions: {bare_hex}. "
        "Replace with var(--studio-*) tokens (e.g. background: var(--studio-white))."
    )


def test_tweaks_js_accent_default_derived_from_css_var():
    """tweaks.js TWEAK_DEFAULTS.accent must derive from the CSS token, not a hardcoded hex.

    rev BLOCK: tweaks.js line 7 hard-codes `accent: '#1f7a4d'`.
    If tokens.css --studio-accent ever changes this default will drift.

    Required pattern:
        getComputedStyle(document.documentElement).getPropertyValue('--studio-accent').trim()
    A fallback literal is permitted as a last-resort guard but the primary read must
    be the CSS var.
    """
    tweaks_path = PROJECT_ROOT / "static" / "tweaks.js"
    if not tweaks_path.exists():
        pytest.skip("tweaks.js does not exist yet")
    content = tweaks_path.read_text(encoding="utf-8")
    assert "getComputedStyle" in content and "--studio-accent" in content, (
        "tweaks.js must derive the accent default from the CSS token via "
        "getComputedStyle(document.documentElement).getPropertyValue('--studio-accent'). "
        "A hardcoded hex fallback is permitted but must not be the sole source."
    )
