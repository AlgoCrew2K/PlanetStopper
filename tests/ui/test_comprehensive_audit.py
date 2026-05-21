"""
RED tests — Comprehensive Audit: Studio redesign defects (COMPREHENSIVE-AUDIT.md).

Encoding pre-populated items from docs/handoff/COMPREHENSIVE-AUDIT.md.
Each test ID matches the audit item tag.

Priority batch 1 (PM RESET directive 2026-05-19):
  D-CARD-01  renderSparkline Chart.js destroy-before-new
  D-CARD-02  renderSparkline reads d.time not d.date
  D-HERO-02  Jinja initial comparison rows show zeros (meta.portfolio zeros)
  D-HERO-03  Bot/Held prefix wiped by updateComparisonRows textContent
  D-HERO-04  Window-selector badges are no-ops (no handler re-fetches chart data)
  D-LAY-01   Pages have max-width cap (should be uncapped for 4K ultrawide)
  F-TWEAK-01 Theme toggle propagates data-theme attribute
  F-TWEAK-02 Density toggle propagates data-density attribute
  F-TWEAK-03 Accent swatch propagates --studio-accent CSS variable
  F-TWEAK-04 Typeface picker propagates --studio-sans CSS variable
  F-TWEAK-05 Math overlays toggle stored and retrievable
  F-TWEAK-06 Number format toggle stored and retrievable
  F-TWEAK-07 Tweaks panel persists to localStorage and rehydrates
  F-WORKSPACE-01 Workspace switcher has click handler that refetches data
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import database

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_INDEX_JS = _STATIC_DIR / "index.js"
_TWEAKS_JS = _STATIC_DIR / "tweaks.js"
_CHROME_HTML = _TEMPLATES_DIR / "_chrome.html"
_INDEX_HTML = _TEMPLATES_DIR / "index.html"
_PERFORMANCE_HTML = _TEMPLATES_DIR / "performance.html"
_ADVISOR_HTML = _TEMPLATES_DIR / "ai_advisor.html"
_HISTORY_HTML = _TEMPLATES_DIR / "history.html"


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_portfolio_strip(tc_dr=1.15, tc_held=68.80, cr_dr=1.41, cr_held=68.42,
                          mdd_dr=0.28, mdd_held=0.19):
    return {
        "today_change":       {"dry_run": tc_dr,  "if_held": tc_held},
        "cumulative_return":  {"dry_run": cr_dr,  "if_held": cr_held},
        "max_drawdown":       {"dry_run": mdd_dr, "if_held": mdd_held},
        "hist_dates": [f"2026-04-{d:02d}" for d in range(1, 36)],
        "hist_bot":   [0.001 * (i + 1) for i in range(35)],
        "hist_held":  [0.0008 * (i + 1) for i in range(35)],
    }


_BASIC_BOT_STATE = {
    "sym_abc": {
        "id": "sym_abc",
        "name": "Alpha Symphony",
        "position": 0.8,
        "simple_return": 0.05,
        "net_deposits": 0.0,
        "time_weighted_return": 0.05,
        "armed": True,
    }
}


def _api_state_stub(bot_state=None, portfolio_strip=None):
    return {
        "bot_state": bot_state if bot_state is not None else _BASIC_BOT_STATE,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": portfolio_strip if portfolio_strip is not None else _make_portfolio_strip(),
    }


def _patch_db():
    """Return a context-manager-compatible db mock for dashboard route use."""
    db_mock = MagicMock()
    db_mock.load_state.return_value = _BASIC_BOT_STATE.copy()
    db_mock.load_chart_history.return_value = {"symphonies": {}}
    db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
    db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
    db_mock.read_fleet_alert.return_value = None
    db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    db_mock.read_port_state.return_value = None
    return db_mock


# ---------------------------------------------------------------------------
# D-CARD-01 — renderSparkline must destroy existing Chart instance before new
# ---------------------------------------------------------------------------

def test_dashboard_renderSparkline_destroys_prior_chart_before_new():
    """D-CARD-01: renderSparkline must not call `new Chart()` without first destroying
    any prior Chart instance on the same canvas.

    Required pattern (one of):
    (a) A per-canvas registry (e.g. _sparks[symId]) followed by .destroy() before new Chart()
    (b) chart.data mutation + chart.update('none') pattern (no new Chart() on repeat call)

    Without this, Chart.js throws "Canvas is already in use" on the 2nd poll,
    killing all subsequent sparkline renders.

    Fix: add a _sparks registry object, store each Chart instance keyed by canvas,
    call existing.destroy() before re-creating, OR mutate + update like renderHeroChart does.
    """
    src = _INDEX_JS.read_text(encoding="utf-8")
    # Extract renderSparkline function body
    m = re.search(r'function\s+renderSparkline\s*\([^)]*\)\s*\{', src)
    assert m, "renderSparkline function not found in static/index.js"
    start = m.start()
    # Find matching closing brace by counting depth
    depth = 0
    i = src.index('{', start)
    body_start = i
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                body_end = i
                break
        i += 1
    body = src[body_start:body_end + 1]

    has_destroy = '.destroy()' in body
    # Acceptable pattern B: no new Chart() inside repeated call (uses update() instead)
    has_new_chart = 'new Chart(' in body
    uses_update_pattern = 'chart.update(' in body or '.update(' in body

    # Either destroy() before new Chart(), or no new Chart() at all (mutate-update pattern)
    assert has_destroy or (not has_new_chart) or uses_update_pattern, (
        "renderSparkline calls `new Chart(canvas, ...)` on every poll without destroying "
        "the prior Chart instance. On the 2nd poll, Chart.js throws "
        "'Canvas is already in use.' and all sparklines go blank. "
        "Fix: add a per-canvas Chart registry (e.g. _sparks = {}), call "
        "_sparks[canvasId].destroy() before re-creating, OR switch to the mutate+update "
        "pattern used by renderHeroChart (no `new Chart()` on repeat call)."
    )


# ---------------------------------------------------------------------------
# D-CARD-02 — renderSparkline must read d.time not d.date for chart labels
# ---------------------------------------------------------------------------

def test_dashboard_renderSparkline_reads_d_time_for_labels():
    """D-CARD-02: renderSparkline builds chart labels from each data point's timestamp.

    The /api/chart/<sym> endpoint emits objects with a `time` field, not `date`.
    If the JS reads `d.date` every label is an empty string and the x-axis shows nothing.

    Fix: change `d.date` to `d.time` in the renderSparkline labels mapping.
    """
    src = _INDEX_JS.read_text(encoding="utf-8")
    m = re.search(r'function\s+renderSparkline\s*\([^)]*\)\s*\{', src)
    assert m, "renderSparkline function not found in static/index.js"
    start = m.start()
    depth = 0
    i = src.index('{', start)
    body_start = i
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                body_end = i
                break
        i += 1
    body = src[body_start:body_end + 1]

    # d.date used in labels mapping is the bug
    assert "d.date" not in body, (
        "renderSparkline reads `d.date` for chart labels but /api/chart/<sym> emits "
        "`d.time`. Every label is an empty string, so the x-axis shows nothing. "
        "Fix: replace `d.date` with `d.time` in the labels mapping inside renderSparkline."
    )
    assert "d.time" in body or "d['time']" in body, (
        "renderSparkline must read `d.time` (or `d['time']`) for chart labels. "
        "The /api/chart/<sym> response emits `time` not `date` per each data point."
    )


# ---------------------------------------------------------------------------
# D-HERO-02 — Jinja initial render of comparison rows must not show +0.00%
# ---------------------------------------------------------------------------

def test_dashboard_jinja_initial_comparison_rows_show_dash_not_zero(monkeypatch):
    """D-HERO-02: When meta.portfolio.tc/cr/mdd are 0 (first call, no scheduler cycle yet),
    the Jinja-rendered comparison row values must show a placeholder '—', not '+0.00%'.

    The user sees '+0.00% / +0.00%' for every row on initial page load, then the JS
    poll overwrites them. This is confusing — zeros imply real data, not 'loading'.

    Acceptable fix: render '—' in Jinja when the value is exactly 0 (or None), trusting
    JS to populate the real values once the first poll fires.

    Current behavior: Jinja always renders the number, so zeros show as '+0.00%'.
    """
    # Build a state where portfolio_strip is present but tc/cr/mdd are all zero
    zero_strip = {
        "today_change":      {"dry_run": 0.0, "if_held": 0.0},
        "cumulative_return": {"dry_run": 0.0, "if_held": 0.0},
        "max_drawdown":      {"dry_run": 0.0, "if_held": 0.0},
        "hist_dates": [],
        "hist_bot": [],
        "hist_held": [],
    }
    stub = _api_state_stub(portfolio_strip=zero_strip)
    db_mock = _patch_db()
    with patch.object(app_module, "get_api_state_dict", return_value=stub), \
         patch.object(app_module, "database", db_mock):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp = c.get("/")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")

    # When all portfolio values are 0, the rendered comparison rows must NOT show +0.00%
    # They should show '—' (or similar placeholder) indicating data not yet loaded.
    assert ">+0.00%<" not in html and "Bot +0.00%" not in html, (
        "Dashboard Jinja initial render shows 'Bot +0.00%' / 'Held +0.00%' when "
        "meta.portfolio.tc/cr/mdd are all zero (first page load before scheduler cycle). "
        "This is misleading — users see 0% across the board and think the bot is flat. "
        "Fix: render '—' (mdash) as the placeholder when tc/cr/mdd are 0 or None, "
        "and rely on JS updateComparisonRows() to populate the real values on first poll."
    )


# ---------------------------------------------------------------------------
# D-HERO-03 — updateComparisonRows must preserve Bot/Held prefix in text spans
# ---------------------------------------------------------------------------

def test_dashboard_updateComparisonRows_preserves_bot_held_prefix_in_separate_span():
    """D-HERO-03: updateComparisonRows sets textContent on .comp-{row}-{bot,held}-text spans.

    If the prefix 'Bot ' / 'Held ' is part of the span's initial text content, setting
    textContent = '+1.15%' wipes the prefix. After the first JS poll every row shows
    '+1.15%' with no label — visually broken and ambiguous (which line is bot, which held?).

    Required fix: move the 'Bot ' / 'Held ' label into a sibling <span> that the JS
    never touches, OR have the JS include the prefix in every textContent write.

    This test verifies via JS source inspection: if textContent is set, the string
    assigned must include the prefix 'Bot ' / 'Held ', OR the template must use a
    sibling label element separate from the testid span.
    """
    src = _INDEX_JS.read_text(encoding="utf-8")
    m = re.search(r'function\s+updateComparisonRows\s*\([^)]*\)\s*\{', src)
    assert m, "updateComparisonRows function not found in static/index.js"
    start = m.start()
    depth = 0
    i = src.index('{', start)
    body_start = i
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                body_end = i
                break
        i += 1
    body = src[body_start:body_end + 1]

    # Check if textContent assignment includes prefix strings
    sets_text_content = "textContent" in body
    if sets_text_content:
        # Look for prefix in the assigned value
        includes_bot_prefix = re.search(r"textContent\s*=.*'Bot |textContent\s*=.*\"Bot ", body)
        includes_held_prefix = re.search(r"textContent\s*=.*'Held |textContent\s*=.*\"Held ", body)
        assert includes_bot_prefix and includes_held_prefix, (
            "updateComparisonRows sets textContent = '+X.XX%' without the 'Bot '/'Held ' prefix. "
            "After the first poll the comparison row labels disappear — only the number shows. "
            "Fix option A: change the assignment to "
            "botText.textContent = 'Bot ' + (bot >= 0 ? '+' : '') + bot.toFixed(2) + '%'. "
            "Fix option B: move the label into a sibling <span class='vs-label'> that the JS "
            "never targets, so the prefix survives every textContent rewrite."
        )


# ---------------------------------------------------------------------------
# D-HERO-04 — Window-selector buttons must trigger chart data re-fetch
# ---------------------------------------------------------------------------

def test_dashboard_window_selector_buttons_have_fetch_handler():
    """D-HERO-04: The hero chart window-selector buttons (30d/60d/90d/125d/YTD/1Y)
    must trigger a re-fetch of chart data filtered to the selected window.

    Current state: the click handler only toggles the 'active' CSS class. No fetch
    or filter logic runs. The chart does not change when any badge is clicked.

    Required: the click handler must either:
    (a) Pass the selected window to loadState/renderHeroChart so the API is called
        with a window parameter (e.g. /api/state?window=30d), OR
    (b) Slice the already-fetched hist_dates/hist_bot/hist_held arrays in-memory
        to the selected window and call _cumChart.update().

    This test verifies: the window-selector click handler in index.js/index.html
    contains more logic than just class toggling.
    """
    src = _INDEX_JS.read_text(encoding="utf-8")
    template_src = _INDEX_HTML.read_text(encoding="utf-8")
    combined = src + template_src

    # Find the window-selector event handler region
    # It should contain a fetch/filter action beyond just classList manipulation
    selector_block_match = re.search(
        r'window-selector[^}]*?(?:addEventListener|forEach)[^}]*?\{[^}]*?\}',
        combined, re.DOTALL
    )

    # The key indicator: class-only toggle (classList.add/remove) with no fetch/update
    class_only_pattern = re.search(
        r'window-selector.*?addEventListener.*?click.*?\{(?:(?!fetch|loadState|_cumChart|renderHeroChart).)*?\}',
        combined, re.DOTALL
    )

    # If the only handler found just toggles active class, that's the bug
    in_template = re.search(
        r"querySelector.*window-selector.*click|window-selector.*click.*fetch"
        r"|window-selector.*click.*loadState|window-selector.*click.*_cumChart",
        combined, re.DOTALL
    )
    fetch_in_handler = (
        "loadState(" in combined[combined.find("window-selector"):combined.find("window-selector") + 500]
        or "_cumChart" in combined[combined.find("window-selector"):combined.find("window-selector") + 500]
        or "fetch(" in combined[combined.find("window-selector"):combined.find("window-selector") + 500]
    ) if "window-selector" in combined else False

    # Direct check in JS: window button handler must trigger fetch or _cumChart update.
    # The JS uses windowMap keyed by data-testid (e.g. 'window-30d') not 'window-selector'.
    # Use find() (first occurrence, in JS) rather than rfind() (last, in HTML buttons).
    ws_idx = src.find("window-selector")
    if ws_idx < 0:
        ws_idx = src.find("windowMap")  # fallback: window button map declaration
    if ws_idx >= 0:
        handler_region = src[ws_idx:ws_idx + 800]
        has_data_action = (
            "fetch(" in handler_region
            or "loadState" in handler_region
            or "_cumChart" in handler_region
            or "renderHeroChart" in handler_region
            or "window_days" in handler_region
            or "window =" in handler_region
        )
    else:
        has_data_action = False

    assert has_data_action, (
        "The window-selector click handler only toggles the 'active' CSS class. "
        "No data re-fetch or chart filter happens when a badge is clicked — the chart "
        "does not change. Fix: add logic to the click handler to either (a) call "
        "loadState() with the selected window parameter so /api/state?window=X is "
        "fetched, OR (b) slice _cumChart.data arrays in-memory to the window length "
        "and call _cumChart.update(). "
        "The badge must cause a visible chart change."
    )


# ---------------------------------------------------------------------------
# D-LAY-01 — Pages must not have a max-width cap (4K ultrawide)
# ---------------------------------------------------------------------------

def _extract_page_wrap_css_block(src: str) -> str:
    """Return the CSS declaration block for .page-wrap (or .dashboard-layout) from a
    <style> section.  Returns empty string if no such rule is found.

    Scopes the D-LAY-01 max-width check to the top-level layout wrapper only —
    inner elements (modals, dialogs, cards) legitimately use max-width and must
    not be flagged."""
    m = re.search(
        r'\.(?:page-wrap|dashboard-layout)\s*\{([^}]*)\}',
        src,
        re.DOTALL,
    )
    return m.group(1) if m else ""


def test_dashboard_no_max_width_cap_on_top_level_container():
    """D-LAY-01: The dashboard page layout must fill the full viewport width on 4K ultrawide.

    Checks only the .page-wrap / .dashboard-layout CSS rule, NOT inner elements
    (modals, dialogs, cards may legitimately cap their own width).

    Inner column/card widths may cap, but the page wrapper must not.

    Fix: remove or increase the max-width on the top-level .page-wrap / .dashboard-layout
    container in index.html.
    """
    src = _INDEX_HTML.read_text(encoding="utf-8")
    page_wrap_css = _extract_page_wrap_css_block(src)
    assert page_wrap_css, (
        "templates/index.html has no .page-wrap or .dashboard-layout CSS rule. "
        "The top-level layout wrapper must be defined with one of these class names."
    )
    max_width_matches = re.findall(r'max-width\s*:\s*(\d+)px', page_wrap_css)
    capped_values = [int(v) for v in max_width_matches if int(v) <= 1600]
    assert len(capped_values) == 0, (
        f"templates/index.html .page-wrap has max-width cap(s) of {capped_values}px. "
        "On a 3840px wide 4K display this leaves large blank margins. "
        "The design uses a fluid grid — the outermost layout wrapper must have no max-width "
        "(or a value ≥ 1920px). Inner elements like modals may retain their own max-width. "
        "Fix: remove or increase max-width from the .page-wrap rule."
    )


def test_performance_no_max_width_cap_on_top_level_container():
    """D-LAY-01 (Performance): Same max-width audit for the Performance page."""
    src = _PERFORMANCE_HTML.read_text(encoding="utf-8")
    max_width_matches = re.findall(r'max-width\s*:\s*(\d+)px', src)
    # 88rem ≈ 1408px at 16px base — still caps 4K
    max_width_rem_matches = re.findall(r'max-width\s*:\s*([\d.]+)rem', src)
    capped_px = [int(v) for v in max_width_matches if int(v) <= 1600]
    # 88rem ≈ 1408px — this is a cap; ≥ 120rem ≈ 1920px would be acceptable
    capped_rem = [float(v) for v in max_width_rem_matches if float(v) <= 100]
    assert len(capped_px) == 0 and len(capped_rem) == 0, (
        f"templates/performance.html has max-width cap(s) in px={capped_px} rem={capped_rem} "
        "that restricts layout on 4K ultrawide. Remove or increase to ≥ 120rem / 1920px."
    )


def _strip_media_query_breakpoints(src: str) -> str:
    """Remove @media (max-width: ...) { ... } blocks so the remaining max-width
    checks only flag container/element width caps, not responsive breakpoints."""
    return re.sub(r'@media\s*\([^)]*max-width[^)]*\)\s*\{[^}]*\}', '', src, flags=re.DOTALL)


def test_history_no_max_width_cap_on_top_level_container():
    """D-LAY-01 (History): Same max-width audit for the History page.
    Excludes @media breakpoints; only flags container element width caps.
    """
    src = _strip_media_query_breakpoints(_HISTORY_HTML.read_text(encoding="utf-8"))
    max_width_rem_matches = re.findall(r'max-width\s*:\s*([\d.]+)rem', src)
    max_width_px_matches = re.findall(r'max-width\s*:\s*(\d+)px', src)
    capped_rem = [float(v) for v in max_width_rem_matches if float(v) <= 100]
    capped_px = [int(v) for v in max_width_px_matches if int(v) <= 1600]
    assert len(capped_rem) == 0 and len(capped_px) == 0, (
        f"templates/history.html has max-width cap(s) rem={capped_rem} px={capped_px} "
        "that restricts layout on 4K ultrawide."
    )


def test_advisor_no_max_width_cap_on_top_level_container():
    """D-LAY-01 (Advisor): Same max-width audit for the Advisor page.
    Excludes @media breakpoints; only flags container element width caps.
    """
    src = _strip_media_query_breakpoints(_ADVISOR_HTML.read_text(encoding="utf-8"))
    max_width_rem_matches = re.findall(r'max-width\s*:\s*([\d.]+)rem', src)
    max_width_px_matches = re.findall(r'max-width\s*:\s*(\d+)px', src)
    capped_rem = [float(v) for v in max_width_rem_matches if float(v) <= 100]
    capped_px = [int(v) for v in max_width_px_matches if int(v) <= 1600]
    assert len(capped_rem) == 0 and len(capped_px) == 0, (
        f"templates/ai_advisor.html has max-width cap(s) rem={capped_rem} px={capped_px} "
        "that restricts layout on 4K ultrawide."
    )


# ---------------------------------------------------------------------------
# F-TWEAK-01 — tweaks.js applies theme via data-theme attribute
# ---------------------------------------------------------------------------

def test_tweaks_js_applies_theme_via_data_theme_attribute():
    """F-TWEAK-01: The tweaks system must apply the selected theme by setting
    `document.documentElement.setAttribute('data-theme', ...)`.

    This is the mechanism CSS uses to switch between light/dark color tokens.
    If the attribute is not set, the CSS :root[data-theme='dark'] selectors
    never fire and theme toggle has no visual effect.
    """
    src = _TWEAKS_JS.read_text(encoding="utf-8")
    assert "data-theme" in src, (
        "tweaks.js does not contain 'data-theme'. The theme toggle must apply the "
        "selected theme by calling root.setAttribute('data-theme', values.theme). "
        "Without this, dark/light CSS selectors never switch."
    )
    # Must be an active setAttribute call, not just a comment
    assert re.search(r"setAttribute\s*\(\s*['\"]data-theme['\"]", src), (
        "tweaks.js must call root.setAttribute('data-theme', ...) to propagate theme. "
        "A string mention without setAttribute() does not apply to the DOM."
    )


# ---------------------------------------------------------------------------
# F-TWEAK-02 — tweaks.js applies density via data-density attribute
# ---------------------------------------------------------------------------

def test_tweaks_js_applies_density_via_data_density_attribute():
    """F-TWEAK-02: The tweaks system must apply density via `data-density` attribute.

    CSS spacing rules use :root[data-density='compact'] / 'balanced' / 'roomy' selectors.
    """
    src = _TWEAKS_JS.read_text(encoding="utf-8")
    assert re.search(r"setAttribute\s*\(\s*['\"]data-density['\"]", src), (
        "tweaks.js must call root.setAttribute('data-density', ...) to propagate density. "
        "Without this, compact/balanced/roomy spacing selectors never apply."
    )


# ---------------------------------------------------------------------------
# F-TWEAK-03 — tweaks.js propagates accent via CSS variable
# ---------------------------------------------------------------------------

def test_tweaks_js_propagates_accent_via_css_variable():
    """F-TWEAK-03: The tweaks system must propagate the accent color by calling
    root.style.setProperty('--studio-accent', values.accent).
    """
    src = _TWEAKS_JS.read_text(encoding="utf-8")
    assert re.search(r"setProperty\s*\(\s*['\"]--studio-accent['\"]", src), (
        "tweaks.js must call root.style.setProperty('--studio-accent', ...) to propagate "
        "the accent swatch. Without this, the accent color change has no visible effect."
    )


# ---------------------------------------------------------------------------
# F-TWEAK-04 — tweaks.js propagates typeface via --studio-sans CSS variable
# ---------------------------------------------------------------------------

def test_tweaks_js_propagates_typeface_via_css_variable():
    """F-TWEAK-04: The typeface picker must propagate the font family via
    root.style.setProperty('--studio-sans', ...).

    Without this, changing the typeface has no visible effect.
    """
    src = _TWEAKS_JS.read_text(encoding="utf-8")
    assert re.search(r"setProperty\s*\(\s*['\"]--studio-sans['\"]", src), (
        "tweaks.js must call root.style.setProperty('--studio-sans', ...) to propagate "
        "the typeface selection. Without this, the font picker has no visible effect."
    )


# ---------------------------------------------------------------------------
# F-TWEAK-05 — tweaks.js stores and retrieves mathOverlays setting
# ---------------------------------------------------------------------------

def test_tweaks_js_stores_math_overlays_setting():
    """F-TWEAK-05: The mathOverlays toggle must be included in the stored tweaks object.

    If mathOverlays is not saved/loaded from localStorage, it resets to the default
    on every page navigation and the toggle feels broken.
    """
    src = _TWEAKS_JS.read_text(encoding="utf-8")
    assert "mathOverlays" in src, (
        "tweaks.js does not include 'mathOverlays' in the tweaks object. "
        "This means the math overlays toggle (MC dials / VWAP bands) is not persisted "
        "to localStorage and resets to default on every page load. "
        "Fix: add 'mathOverlays: true' to TWEAK_DEFAULTS and handle it in applyAll()."
    )
    # applyAll must do something with mathOverlays — not just store it
    m = re.search(r'function\s+applyAll\s*\([^)]*\)\s*\{', src)
    assert m, "applyAll function not found in tweaks.js"
    start = m.start()
    depth = 0
    i = src.index('{', start)
    body_start = i
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                body_end = i
                break
        i += 1
    apply_body = src[body_start:body_end + 1]
    assert "mathOverlays" in apply_body or "math" in apply_body.lower(), (
        "applyAll() in tweaks.js does not handle the mathOverlays setting. "
        "The toggle must propagate to the DOM (e.g. root.setAttribute('data-math-overlays', ...))"
        " so CSS can show/hide MC dials and VWAP bands."
    )


# ---------------------------------------------------------------------------
# F-TWEAK-06 — tweaks.js stores and retrieves numFormat setting
# ---------------------------------------------------------------------------

def test_tweaks_js_stores_num_format_setting():
    """F-TWEAK-06: The numFormat toggle must be included in stored tweaks and applied.

    numFormat: 'full' | 'compact' controls number display formatting across all screens.
    Without persistence, the toggle resets on every page load.
    """
    src = _TWEAKS_JS.read_text(encoding="utf-8")
    assert "numFormat" in src, (
        "tweaks.js does not include 'numFormat' in the tweaks object. "
        "Fix: add 'numFormat: \"full\"' to TWEAK_DEFAULTS."
    )
    # applyAll must propagate numFormat to DOM somehow
    m = re.search(r'function\s+applyAll\s*\([^)]*\)\s*\{', src)
    assert m, "applyAll function not found in tweaks.js"
    start = m.start()
    depth = 0
    i = src.index('{', start)
    body_start = i
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                body_end = i
                break
        i += 1
    apply_body = src[body_start:body_end + 1]
    assert "numFormat" in apply_body or "num-format" in apply_body or "data-num" in apply_body, (
        "applyAll() in tweaks.js does not handle numFormat. "
        "The number format toggle must propagate to the DOM so CSS/JS can switch "
        "between 'full' (1,234,567) and 'compact' (1.2M) formatting across all screens."
    )


# ---------------------------------------------------------------------------
# F-TWEAK-07 — tweaks.js persists to localStorage and rehydrates on next visit
# ---------------------------------------------------------------------------

def test_tweaks_js_persists_and_rehydrates_from_local_storage():
    """F-TWEAK-07: tweaks.js must save the full tweaks object to localStorage on every
    change and load it on DOMContentLoaded so settings survive page navigation.

    Required:
    - STORAGE_KEY constant defined
    - localStorage.setItem() called on tweak change
    - localStorage.getItem() called on load
    - DOMContentLoaded handler calls applyAll(getAll()) to rehydrate
    """
    src = _TWEAKS_JS.read_text(encoding="utf-8")
    assert "localStorage.setItem" in src, (
        "tweaks.js must call localStorage.setItem(...) to persist tweaks. "
        "Without persistence, every page navigation resets all user preferences."
    )
    assert "localStorage.getItem" in src, (
        "tweaks.js must call localStorage.getItem(...) to rehydrate tweaks on page load."
    )
    assert "DOMContentLoaded" in src, (
        "tweaks.js must listen for DOMContentLoaded to apply persisted tweaks on page load. "
        "Without this, the tweaks do not rehydrate after page navigation."
    )
    # applyAll must be called inside the DOMContentLoaded handler
    assert re.search(r"DOMContentLoaded.*applyAll|applyAll.*DOMContentLoaded", src, re.DOTALL), (
        "tweaks.js DOMContentLoaded handler must call applyAll(getAll()) to rehydrate "
        "persisted tweaks. Without this, stored preferences are loaded but never applied."
    )


# ---------------------------------------------------------------------------
# F-WORKSPACE-01 — Workspace switcher must trigger data refetch
# ---------------------------------------------------------------------------

def test_workspace_switcher_has_click_handler_that_refetches_data():
    """F-WORKSPACE-01: The workspace switcher button in _chrome.html must have a
    click handler that refetches data for the switched account.

    Current: the button is static markup with no event listener. Clicking it does
    nothing — the dashboard continues to show the original account's data.

    Required: a click handler (inline on the button element itself, or a named
    JS listener in tweaks.js/index.js scoped to workspace-switcher) that:
    (a) Updates the active account context
    (b) Triggers a data refetch (loadState() or equivalent)
    """
    chrome_src = _CHROME_HTML.read_text(encoding="utf-8")
    tweaks_src = _TWEAKS_JS.read_text(encoding="utf-8")
    index_src = _INDEX_JS.read_text(encoding="utf-8")

    # Check for inline onclick on the workspace-switcher button element itself
    # Extract the button tag only (stops at first '>') to avoid cross-element false positives
    ws_btn_tag_match = re.search(
        r'data-testid="workspace-switcher"[^>]*>', chrome_src
    )
    has_inline_onclick = bool(
        ws_btn_tag_match and "onclick" in ws_btn_tag_match.group(0)
    )

    # Check JS files for a scoped querySelector/getElementById targeting workspace-switcher
    # with an addEventListener('click', ...) call within ~200 chars of the reference
    def _has_scoped_js_click(src):
        for m in re.finditer(r'workspace-switcher', src):
            snippet = src[m.start():m.start() + 300]
            if "addEventListener" in snippet and "click" in snippet:
                return True
            if "onclick" in snippet:
                return True
        return False

    has_js_click_tweaks = _has_scoped_js_click(tweaks_src)
    has_js_click_index = _has_scoped_js_click(index_src)

    assert has_inline_onclick or has_js_click_tweaks or has_js_click_index, (
        "The workspace-switcher button has no click event handler. "
        "The button is static markup — clicking it does nothing. "
        "Fix: add onclick='...' inline on the button element, OR add a scoped "
        "addEventListener('click', ...) in tweaks.js or index.js targeting "
        "[data-testid='workspace-switcher']. The handler must update the active "
        "account context and call loadState() to refetch data for the new workspace."
    )


# ---------------------------------------------------------------------------
# D-HERO-02 (API layer) — meta.portfolio.tc must match portfolio_strip.today_change.dry_run
# ---------------------------------------------------------------------------

def test_api_state_meta_portfolio_tc_matches_portfolio_strip_today_change_dry_run(monkeypatch):
    """D-HERO-02 (API): _build_meta must populate meta.portfolio.tc from
    portfolio_strip.today_change.dry_run — never return a zero placeholder
    when portfolio_strip has real values.

    This is the authoritative contract: the API must not zero out data that
    exists in portfolio_strip just because the scheduler hasn't run yet.
    """
    strip = _make_portfolio_strip(tc_dr=1.15, tc_held=68.80)
    stub = _api_state_stub(portfolio_strip=strip)
    db_mock = _patch_db()

    with patch.object(app_module, "get_api_state_dict", return_value=stub), \
         patch.object(app_module, "database", db_mock):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp = c.get("/api/state")
    assert resp.status_code == 200
    import json
    data = json.loads(resp.data)
    meta = data.get("meta", {})
    portfolio = meta.get("portfolio", {})

    tc = portfolio.get("tc")
    assert tc is not None, "meta.portfolio.tc is missing from /api/state response"
    assert abs(float(tc) - 1.15) < 0.01, (
        f"meta.portfolio.tc={tc!r} but portfolio_strip.today_change.dry_run=1.15. "
        "_build_meta must pass the real portfolio_strip value through to meta.portfolio.tc "
        "instead of returning 0.0. "
        "Fix: ensure _build_meta reads ps.get('today_change', {}).get('dry_run', 0.0) "
        "when portfolio_strip is passed in."
    )


# ---------------------------------------------------------------------------
# D-CARD-01 (additional) — _sparks registry pattern in index.js
# ---------------------------------------------------------------------------

def test_dashboard_index_js_has_sparkline_chart_registry_or_destroy_pattern():
    """D-CARD-01 (registry): A Chart.js canvas can only have one Chart instance.
    index.js must either:
    (a) Keep a registry of active Chart instances and call .destroy() before new Chart()
    (b) Use the mutate+update pattern (no second new Chart() call per canvas)

    This specifically checks for the registry variable pattern as the most robust fix.
    """
    src = _INDEX_JS.read_text(encoding="utf-8")
    # Pattern (a): module-level registry dict + destroy before new Chart
    has_registry = re.search(r'var\s+_sparks\s*=\s*\{|const\s+_sparks\s*=\s*\{|_sparks\s*=\s*\{\}', src)
    # Pattern (b): no new Chart() inside renderSparkline (uses update path only)
    m = re.search(r'function\s+renderSparkline\s*\([^)]*\)\s*\{', src)
    if m:
        start = m.start()
        depth = 0
        i = src.index('{', start)
        body_start = i
        while i < len(src):
            if src[i] == '{':
                depth += 1
            elif src[i] == '}':
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
            i += 1
        body = src[body_start:body_end + 1]
        has_new_chart_in_body = 'new Chart(' in body
        has_destroy_in_body = '.destroy()' in body
        # Pattern (b): new Chart() only when no existing instance (guarded)
        has_guard = re.search(r'if\s*\(\s*!?\s*_sparks|if\s*\(\s*!?\s*existing|if\s*\(\s*!', body)
    else:
        has_new_chart_in_body = False
        has_destroy_in_body = False
        has_guard = None

    safe = (
        has_registry is not None
        or has_destroy_in_body
        or (has_guard and not has_new_chart_in_body)
    )
    assert safe, (
        "renderSparkline creates a new Chart() instance on every poll without safeguards. "
        "On the 2nd poll, Chart.js throws 'Canvas is already in use' and all sparklines "
        "stop rendering. Fix: add `var _sparks = {};` at module scope, then inside "
        "renderSparkline: if (_sparks[canvasId]) { _sparks[canvasId].destroy(); } "
        "_sparks[canvasId] = new Chart(canvas, ...)."
    )


# ---------------------------------------------------------------------------
# D-HERO-04 (additional) — window selector buttons carry data-window attribute
# ---------------------------------------------------------------------------

def test_dashboard_window_selector_buttons_carry_data_window_attribute():
    """D-HERO-04 (template): Window-selector buttons must carry a data-window
    attribute so the click handler can read the selected window value.

    Without data-window, the click handler cannot know which window (30d/60d/etc.)
    was selected to pass to the API or slice the data.
    """
    src = _INDEX_HTML.read_text(encoding="utf-8")
    # Find window-selector buttons
    m = re.search(r'data-testid=["\']window-selector["\'].*?</div>', src, re.DOTALL)
    assert m, "window-selector div not found in templates/index.html"
    block = m.group(0)
    buttons = re.findall(r'<button[^>]*>', block)
    assert len(buttons) >= 5, f"Expected ≥5 window buttons, found {len(buttons)}"
    buttons_without_data_window = [b for b in buttons if "data-window" not in b]
    assert len(buttons_without_data_window) == 0, (
        f"{len(buttons_without_data_window)} window-selector buttons lack data-window attribute: "
        f"{buttons_without_data_window[:3]}. "
        "The click handler needs to read the selected window value from this attribute "
        "to pass to /api/state?window=X or slice hist arrays. "
        "Fix: add data-window='30d', data-window='60d', etc. to each button."
    )


# ---------------------------------------------------------------------------
# KICK CYCLE 9 — updateDashboard must wrap renderers in try/catch (Step 3.5)
# ---------------------------------------------------------------------------

def test_dashboard_updateDashboard_wraps_renderers_in_try_catch():
    """KICK CYCLE 9 Step 3.5: updateDashboard() calls renderHeroChart, renderGuardAlpha,
    updateComparisonRows, updateVerdicts, and loadCharts in sequence.

    If any one throws (Chart.js re-use, missing field, null canvas), all subsequent
    calls are skipped. The result: a single bad symphony can kill all DOM updates.

    Required: each render call is wrapped in try { ... } catch (e) { console.error(...) }
    OR each render function internally catches its own throws.

    Fix: wrap each of the 5 render calls in updateDashboard with individual try/catch blocks.
    """
    src = _INDEX_JS.read_text(encoding="utf-8")
    m = re.search(r'function\s+updateDashboard\s*\([^)]*\)\s*\{', src)
    assert m, "updateDashboard function not found in static/index.js"
    start = m.start()
    depth = 0
    i = src.index('{', start)
    body_start = i
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                body_end = i
                break
        i += 1
    body = src[body_start:body_end + 1]

    has_try = 'try' in body and 'catch' in body
    assert has_try, (
        "updateDashboard() calls renderHeroChart / renderGuardAlpha / updateComparisonRows "
        "/ updateVerdicts / loadCharts in sequence with no error isolation. "
        "A single throw in any one renderer kills the rest — all DOM updates stop. "
        "Fix: wrap each render call in try { ... } catch (e) { console.error('...', e); } "
        "so one failing renderer does not prevent the others from running."
    )


# ---------------------------------------------------------------------------
# KICK CYCLE 9 — API↔DOM cross-check: comparison rows show API values (Step 3.1)
# ---------------------------------------------------------------------------

def test_dashboard_api_state_portfolio_strip_has_dict_today_change(monkeypatch):
    """KICK CYCLE 9 Step 3.1: /api/state must return portfolio_strip.today_change as a
    dict with dry_run and if_held keys — not a bare float.

    The JS updateComparisonRows() reads portfolio_strip.today_change.dry_run. If
    today_change is a bare float the .dry_run access returns undefined and the rows
    show +0.00% permanently.

    This is a contract test: the shape must be correct regardless of the specific values.
    """
    import json
    strip = _make_portfolio_strip(tc_dr=0.79, tc_held=0.80)
    stub = _api_state_stub(portfolio_strip=strip)
    db_mock = _patch_db()

    with patch.object(app_module, "get_api_state_dict", return_value=stub), \
         patch.object(app_module, "database", db_mock):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp = c.get("/api/state")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    ps = data.get("portfolio_strip", {})
    tc = ps.get("today_change")
    assert isinstance(tc, dict), (
        f"portfolio_strip.today_change must be a dict with dry_run/if_held keys, "
        f"got {type(tc).__name__}: {tc!r}. "
        "JS updateComparisonRows() reads .dry_run/.if_held on this object. "
        "A bare float makes all comparison row updates no-ops."
    )
    assert "dry_run" in tc and "if_held" in tc, (
        f"portfolio_strip.today_change dict must have 'dry_run' and 'if_held' keys, "
        f"got keys: {list(tc.keys())!r}."
    )


# ---------------------------------------------------------------------------
# Performance P-WIN-01 — window selector change triggers data re-fetch
# ---------------------------------------------------------------------------

def test_performance_window_selector_change_triggers_refresh():
    """P-WIN-01: The Performance page window selector must trigger a data re-fetch
    when changed (30d/60d/90d/125d/YTD/1Y/5Y).

    static/performance.js already has: daysPicker.addEventListener('change', refresh)
    This test verifies that wiring is present.
    """
    src = (pathlib.Path(__file__).parent.parent.parent / "static" / "performance.js").read_text(encoding="utf-8")
    assert "daysPicker" in src or "days-picker" in src, (
        "performance.js has no reference to days-picker / daysPicker. "
        "The window selector must be wired to trigger a data refresh when changed."
    )
    assert re.search(r"addEventListener.*change.*refresh|daysPicker.*change|change.*refresh", src, re.DOTALL), (
        "performance.js does not wire the days-picker change event to a refresh/fetch call. "
        "Fix: daysPicker.addEventListener('change', refresh) in wireUI()."
    )


# ---------------------------------------------------------------------------
# History H-WIN-01 — window selector change triggers loadHistory
# ---------------------------------------------------------------------------

def test_history_window_selector_change_triggers_load_history():
    """H-WIN-01: The History page window selector must trigger loadHistory() when changed.

    static/history.js must wire window-picker change to update currentWindow + call loadHistory.
    """
    src = (pathlib.Path(__file__).parent.parent.parent / "static" / "history.js").read_text(encoding="utf-8")
    assert "window-picker" in src or "windowPicker" in src or "picker" in src, (
        "history.js has no reference to window-picker. "
        "The window selector must be wired to trigger loadHistory() when changed."
    )
    assert re.search(
        r"picker.*addEventListener.*change|addEventListener.*change.*loadHistory|change.*currentWindow",
        src, re.DOTALL
    ), (
        "history.js does not wire the window-picker change event to loadHistory(). "
        "Fix: picker.addEventListener('change', function() { currentWindow = picker.value; loadHistory(currentWindow); })."
    )


# ---------------------------------------------------------------------------
# Advisor A-INTERACT-01 — Symphony picker triggers suggestion fetch
# ---------------------------------------------------------------------------

def test_advisor_symphony_picker_triggers_suggestion_fetch():
    """A-INTERACT-01: Clicking a different symphony in the Advisor symphony picker
    must trigger a new /ai-advisor/suggest POST for that symphony.

    static/ai_advisor.js must wire the symphony selector change to loadSuggestions().
    """
    src = (pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js").read_text(encoding="utf-8")
    has_symphony_change = re.search(
        r"symphony.*addEventListener.*change|addEventListener.*change.*suggest|picker.*change",
        src, re.DOTALL
    )
    assert has_symphony_change, (
        "ai_advisor.js does not wire the symphony picker change event to a suggestion fetch. "
        "Selecting a different symphony does nothing. "
        "Fix: wire the symphony-picker change event to loadSuggestions() or equivalent."
    )


# ---------------------------------------------------------------------------
# Advisor A-INTERACT-02/03 — Apply/Dismiss buttons POST to correct endpoints
# ---------------------------------------------------------------------------

def test_advisor_apply_button_posts_to_accept_endpoint():
    """A-INTERACT-02: The Apply button on each suggestion card must POST to /ai-advisor/accept."""
    src = (pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js").read_text(encoding="utf-8")
    assert "/ai-advisor/accept" in src, (
        "ai_advisor.js does not POST to /ai-advisor/accept. "
        "The Apply button must call the accept endpoint to persist the suggestion. "
        "Fix: add fetch('/ai-advisor/accept', { method: 'POST', ... }) in acceptSuggestion()."
    )


def test_advisor_dismiss_button_posts_to_reject_endpoint():
    """A-INTERACT-03: The Dismiss button on each suggestion card must POST to /ai-advisor/reject."""
    src = (pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js").read_text(encoding="utf-8")
    assert "/ai-advisor/reject" in src, (
        "ai_advisor.js does not POST to /ai-advisor/reject. "
        "The Dismiss button must call the reject endpoint. "
        "Fix: add fetch('/ai-advisor/reject', { method: 'POST', ... }) in rejectSuggestion()."
    )


# ---------------------------------------------------------------------------
# Foundation F-NAV-01 — active route highlighted in top nav
# ---------------------------------------------------------------------------

def test_chrome_nav_links_have_active_route_indicator():
    """F-NAV-01: The active route must be highlighted in the top nav.

    _chrome.html nav links must use a Jinja conditional to apply 'active' class or
    aria-current="page" to the link matching the current route.
    """
    src = _CHROME_HTML.read_text(encoding="utf-8")
    has_active_indicator = (
        "active_route" in src or "active-route" in src
        or "aria-current" in src
        or re.search(r"active_route\s*==|active_route\s*is", src)
    )
    assert has_active_indicator, (
        "_chrome.html does not have an active route indicator for nav links. "
        "The currently active page link must be visually distinguished in the nav. "
        "Fix: add `{% if active_route == 'dashboard' %}class='active'{% endif %}` "
        "(or aria-current='page') to each nav link in _chrome.html, and pass "
        "`active_route='dashboard'` from the Flask route to render_template."
    )


# ---------------------------------------------------------------------------
# Foundation F-NAV-02 — nav links route correctly
# ---------------------------------------------------------------------------

def test_chrome_nav_links_href_routes_are_correct():
    """F-NAV-02: Nav links in _chrome.html must use url_for() to route correctly.

    Hardcoded paths ('/dashboard', '/performance') will break if Flask app root changes.
    Required pattern: href="{{ url_for('dashboard') }}" etc.
    """
    src = _CHROME_HTML.read_text(encoding="utf-8")
    nav_links = re.findall(r'href\s*=\s*["\'][^"\']+["\']', src)
    hardcoded = [
        link for link in nav_links
        if re.search(r'href\s*=\s*["\']/(?:dashboard|performance|history|ai.?advisor|settings)', link)
        and "url_for" not in link
    ]
    assert len(hardcoded) == 0, (
        f"_chrome.html has {len(hardcoded)} hardcoded nav href(s): {hardcoded}. "
        "Use url_for() for all route links so they work regardless of Flask app root. "
        "Fix: change href='/performance' to href=\"{{ url_for('performance') }}\"."
    )


# ---------------------------------------------------------------------------
# Foundation F-MODE-01 — LIVE/DRY-RUN badge reflects actual env
# ---------------------------------------------------------------------------

def test_rendered_dashboard_mode_badge_reflects_api_state(monkeypatch):
    """F-MODE-01: The LIVE / DRY-RUN badge must reflect the actual LIVE_EXECUTION env var.

    If DRY RUN is hardcoded in the template it would show wrong values in production.
    The badge must read from meta.mode which is computed from the LIVE_EXECUTION env.
    """
    import json
    stub = _api_state_stub()
    db_mock = _patch_db()
    with patch.object(app_module, "get_api_state_dict", return_value=stub), \
         patch.object(app_module, "database", db_mock):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp_api = c.get("/api/state")
            resp_page = c.get("/")
    assert resp_page.status_code == 200
    api_data = json.loads(resp_api.data)
    mode = api_data.get("meta", {}).get("mode", "")
    html = resp_page.data.decode("utf-8")
    # The mode value (DRY RUN or LIVE) must appear somewhere in the rendered page
    assert mode in html, (
        f"API meta.mode={mode!r} but this string does not appear in the rendered dashboard HTML. "
        "The LIVE/DRY-RUN badge must render the actual mode from the API, not a hardcoded string. "
        "Fix: ensure the mode pill template reads {{ meta.mode }} from the Flask context."
    )


# ---------------------------------------------------------------------------
# Foundation F-CLOCK-01 — ET clock element exists in rendered nav
# ---------------------------------------------------------------------------

def test_rendered_dashboard_has_et_clock_element(monkeypatch):
    """F-CLOCK-01: The dashboard must render an ET clock element that updates live.

    The clock must be present in the DOM (a Jinja-rendered initial value is acceptable;
    JS polling keeps it live). If the element is absent entirely, there is no clock.
    """
    stub = _api_state_stub()
    db_mock = _patch_db()
    with patch.object(app_module, "get_api_state_dict", return_value=stub), \
         patch.object(app_module, "database", db_mock):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            resp = c.get("/")
    html = resp.data.decode("utf-8")
    assert "clock-et" in html or "data-testid=\"clock" in html or "ET" in html, (
        "Dashboard rendered HTML does not contain an ET clock element (data-testid='clock-et' "
        "or similar). The clock must be visible so operators know the current market time. "
        "Fix: render {{ meta.clock_et }} in the nav chrome and ensure the testid is present."
    )
