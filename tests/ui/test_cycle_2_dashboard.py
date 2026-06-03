"""
Cycle 2 — Dashboard parity RED tests.

Source: feature-plans/studio-design-handoff.md AC-S.4
Design: .design-handoff/project/studio.jsx + detail-panel.jsx + mock-data.js
Target: templates/index.html vs /api/state

All tests are written against REQUIRED behaviour per the design spec.
Tests are intentionally RED until impl delivers the Dashboard template rewrite.
"""

import re
from pathlib import Path
import pytest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Portfolio strip with hist_ chart data needed by Hero section.
_PORTFOLIO_STRIP = {
    "today_change": {"if_held": 0.45, "dry_run": 0.61},
    "cumulative_return": {"if_held": 12.3, "dry_run": 15.8},
    "max_drawdown": {"if_held": -4.2, "dry_run": -3.1},
    "hist_dates": ["2025-01-01", "2025-02-01", "2025-03-01"],
    "hist_bot": [0.0, 5.2, 15.8],
    "hist_held": [0.0, 4.1, 12.3],
}

_ACTIVE_SYM = {
    "name": "sym_alpha",
    "normalized_name": "Alpha Strategy",
    "armed": True,
    "tp_armed": False,
    "para_armed": False,
    "triggered": False,
    "triggered_reason": None,
    "guard_alpha": 3.5,
    "_tc": {"if_held": 0.45, "dry_run": 0.61},
    "_cr": {"if_held": 12.3, "dry_run": 15.8},
    "_mdd": {"if_held": -4.2, "dry_run": -3.1},
}

_TRIGGERED_SYM = {
    "name": "sym_beta",
    "normalized_name": "Beta Strategy",
    "armed": False,
    "tp_armed": False,
    "para_armed": False,
    "triggered": True,
    "triggered_reason": "Take-Profit target reached",
    "guard_alpha": 1.2,
    "_tc": {"if_held": 0.30, "dry_run": 0.55},
    "_cr": {"if_held": 8.1, "dry_run": 9.3},
    "_mdd": {"if_held": -3.1, "dry_run": -2.2},
}

_STANDBY_SYM = {
    "name": "sym_gamma",
    "normalized_name": "Gamma Strategy",
    "armed": False,
    "tp_armed": False,
    "para_armed": False,
    "triggered": False,
    "triggered_reason": None,
    "guard_alpha": 0.0,
    "_tc": {"if_held": 0.10, "dry_run": 0.10},
    "_cr": {"if_held": 5.0, "dry_run": 5.0},
    "_mdd": {"if_held": -2.0, "dry_run": -2.0},
}

_API_STATE_ACTIVE = {
    "status": "active",
    "market_state": "open",
    "live_mode": False,
    "next_run_seconds": 37,
    "data_as_of": "2025-05-19",
    "is_locked": False,
    "bot_state": {
        "sym_alpha": _ACTIVE_SYM,
        "sym_beta": _TRIGGERED_SYM,
        "sym_gamma": _STANDBY_SYM,
    },
    "port_state": {},
    "exit_authority": {},
    "daemon_started_at": "2025-05-19T09:00:00",
    "portfolio_strip": _PORTFOLIO_STRIP,
    "meta": {
        "mode": "DRY RUN",
        "system_online": True,
        "tracked": 3,
        "armed": 1,
        "triggered": 1,
        "next_run": "00:37",
        "account": {"label": "Main Account", "uuid_short": "ab12cd34"},
        "market_state": "open",
        "market_state_label": "Market open",
        "clock_et": "10:00:00 AM ET",
        "portfolio": {
            "tc": 0.61, "tc_if_held": 0.45,
            "cr": 15.8, "cr_if_held": 12.3,
            "mdd": -3.1, "mdd_if_held": -4.2,
            "hist_dates": ["2025-01-01", "2025-02-01", "2025-03-01"],
            "hist_bot": [0.0, 5.2, 15.8],
            "hist_held": [0.0, 4.1, 12.3],
            "data_as_of": "2025-05-19",
        },
    },
    "symphonies": [_ACTIVE_SYM, _TRIGGERED_SYM, _STANDBY_SYM],
    "shadow_divergence": None,
}


@pytest.fixture
def dashboard_client():
    import app as app_module
    app_module.app.config["TESTING"] = True
    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_ACTIVE):
        with app_module.app.test_client() as c:
            yield c


# ---------------------------------------------------------------------------
# AC-S.4.1 — Dashboard route responds 200
# ---------------------------------------------------------------------------


def test_dashboard_route_200(dashboard_client):
    resp = dashboard_client.get("/")
    assert resp.status_code == 200, "Dashboard / must return 200"


# ---------------------------------------------------------------------------
# AC-S.4.2 — Hero section present
# ---------------------------------------------------------------------------


def test_dashboard_has_guard_alpha_headline(dashboard_client):
    """Hero section must contain the Guard Alpha headline landmark."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="guard-alpha-headline"' in html, (
        "Dashboard must render <element data-testid=\"guard-alpha-headline\"> "
        "containing the Bot-minus-If-Held cumulative alpha value. "
        "Design: studio.jsx Hero section, large alpha figure."
    )


def test_dashboard_has_hero_section(dashboard_client):
    """Hero section landmark must be present."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="hero-section"' in html, (
        "Dashboard must render <section data-testid=\"hero-section\"> "
        "wrapping the Guard Alpha headline, window selector, cumulative chart, "
        "and Bot-vs-Held comparison rows."
    )


def test_dashboard_hero_has_window_selector(dashboard_client):
    """Hero section must include a time-window selector."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="window-selector"' in html, (
        "Hero section must contain data-testid=\"window-selector\" for "
        "selecting 30d / 60d / 90d / 125d / YTD / 1Y windows. "
        "Design: studio.jsx SegControl inside Hero."
    )


def test_dashboard_hero_has_cumulative_chart(dashboard_client):
    """Hero section must include the Bot-vs-Held cumulative chart."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="cum-chart"' in html, (
        "Hero section must contain data-testid=\"cum-chart\" — the 60d "
        "Bot-vs-Held cumulative return chart. "
        "Design: studio.jsx CumChart component."
    )


# ---------------------------------------------------------------------------
# AC-S.4.3 — Bot-vs-Held comparison rows (Today / Cumulative / Max DD)
# ---------------------------------------------------------------------------


def test_dashboard_hero_has_vs_rows(dashboard_client):
    """Hero must contain 4 Bot-vs-Held comparison rows after Phase 2.

    Phase 2 (ux-design-deliverable.md §Change 4) adds a 4th vs-row for
    Annualized Volatility (Ann. Vol), after the existing Today / Cumulative /
    Max DD rows. The Phase 1 assertion of exactly 3 rows is superseded.
    """
    html = dashboard_client.get("/").data.decode("utf-8")
    vs_rows = re.findall(r'data-testid="vs-row"', html)
    assert len(vs_rows) == 4, (
        f"Hero section must contain 4 data-testid=\"vs-row\" elements after Phase 2 "
        f"(Today / Cumulative / Max DD / Ann. Vol). Found: {len(vs_rows)}. "
        "Source: ux-design-deliverable.md §Change 4 — add volatility vs-row."
    )


def test_dashboard_vs_rows_have_labels(dashboard_client):
    """Bot-vs-Held rows must include Today, Cumulative, Max DD labels."""
    html = dashboard_client.get("/").data.decode("utf-8")
    for label in ["Today", "Cumulative", "Max DD"]:
        assert label in html, (
            f"Hero Bot-vs-Held section must include '{label}' row label. "
            "Design: studio.jsx VsRow labels."
        )


# ---------------------------------------------------------------------------
# AC-S.4.4 — Symphony card sections (Active vs Standby)
# ---------------------------------------------------------------------------


def test_dashboard_has_active_section(dashboard_client):
    """Dashboard must render an Active symphonies section."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="active-section"' in html, (
        "Dashboard must render <section data-testid=\"active-section\"> "
        "containing armed/triggered symphony cards. "
        "Design: studio.jsx active cards grid."
    )


def test_dashboard_has_standby_section(dashboard_client):
    """Dashboard must render a Standby symphonies section."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="standby-section"' in html, (
        "Dashboard must render <section data-testid=\"standby-section\"> "
        "containing non-armed, non-triggered symphony cards. "
        "Design: studio.jsx standby cards grid."
    )


def test_dashboard_active_section_has_cards(dashboard_client):
    """Active section must contain at least one symphony card."""
    html = dashboard_client.get("/").data.decode("utf-8")
    cards = re.findall(r'data-testid="sym-card"', html)
    assert len(cards) >= 1, (
        "Dashboard must render at least one data-testid=\"sym-card\" element. "
        "Design: studio.jsx SymphonyCard."
    )


def test_dashboard_cards_count_matches_symphonies(dashboard_client):
    """Total symphony card count must match symphony count in API state."""
    html = dashboard_client.get("/").data.decode("utf-8")
    cards = re.findall(r'data-testid="sym-card"', html)
    assert len(cards) == 3, (
        f"Dashboard must render one sym-card per symphony in bot_state. "
        f"Expected 3 (2 active/triggered + 1 standby), found {len(cards)}."
    )


# ---------------------------------------------------------------------------
# AC-S.4.5 — Symphony card content
# ---------------------------------------------------------------------------


def test_dashboard_cards_have_status_pill(dashboard_client):
    """Each symphony card must include a status pill."""
    html = dashboard_client.get("/").data.decode("utf-8")
    pills = re.findall(r'data-testid="status-pill"', html)
    assert len(pills) >= 1, (
        "Symphony cards must contain data-testid=\"status-pill\" showing "
        "ARMED / TP-ARMED / PARA-ARMED / TRAILING STOP / TAKE-PROFIT / STANDBY. "
        "Design: studio.jsx statusInfo() → pill."
    )


def test_dashboard_triggered_card_has_verdict(dashboard_client):
    """Triggered symphony card must show good-call/early-exit verdict."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="triggered-verdict"' in html, (
        "A triggered symphony card must include data-testid=\"triggered-verdict\" "
        "showing the good-call / early-exit assessment. "
        "Design: studio.jsx triggered card verdict row."
    )


def test_dashboard_cards_have_dual_value_headline(dashboard_client):
    """Symphony cards must show dual Bot-vs-Held value headline."""
    html = dashboard_client.get("/").data.decode("utf-8")
    headlines = re.findall(r'data-testid="dual-value-headline"', html)
    assert len(headlines) >= 1, (
        "Symphony cards must contain data-testid=\"dual-value-headline\" "
        "showing Bot and If-Held cumulative returns side by side. "
        "Design: studio.jsx card dual-value block."
    )


def test_dashboard_active_cards_have_cash_now_button(dashboard_client):
    """Active (armed/triggered) symphony cards must have a Cash Now button."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="cash-now-btn"' in html, (
        "Active symphony cards must contain data-testid=\"cash-now-btn\". "
        "Button is present but 0-click (no wiring in cycle 2). "
        "Design: studio.jsx card top-right Cash Now button."
    )


# ---------------------------------------------------------------------------
# AC-S.4.6 — Detail slide-over panel
# ---------------------------------------------------------------------------


def test_dashboard_has_detail_panel(dashboard_client):
    """Dashboard must include the detail slide-over panel element."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="detail-panel"' in html, (
        "Dashboard must render data-testid=\"detail-panel\" — the 760px "
        "slide-over that opens when a symphony card is clicked. "
        "Design: detail-panel.jsx DetailPanel component."
    )


def test_detail_panel_has_sticky_stats_row(dashboard_client):
    """Detail panel must contain the sticky 4-stat row."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="detail-stats-row"' in html, (
        "Detail panel must contain data-testid=\"detail-stats-row\" — "
        "sticky row with 4 key stats at the top of the panel. "
        "Design: detail-panel.jsx sticky header stats."
    )


def test_detail_panel_has_intraday_chart(dashboard_client):
    """Detail panel must contain the intraday tape chart."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="intraday-chart"' in html, (
        "Detail panel must contain data-testid=\"intraday-chart\" — "
        "the intraday tape with live/shadow/stop/breakeven/VWAP overlays. "
        "Design: detail-panel.jsx intraday chart area."
    )


def test_detail_panel_has_events_timeline(dashboard_client):
    """Detail panel must contain today's events timeline."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="events-timeline"' in html, (
        "Detail panel must contain data-testid=\"events-timeline\" — "
        "today's decision events list. "
        "Design: detail-panel.jsx events section."
    )


def test_detail_panel_has_vars_section(dashboard_client):
    """Detail panel must contain the .env/SQLite vars section."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="vars-section"' in html, (
        "Detail panel must contain data-testid=\"vars-section\" showing "
        "all .env and SQLite variables with locked-from-autotuner icon and Edit. "
        "Design: detail-panel.jsx vars panel."
    )


def test_detail_panel_has_close_mechanism(dashboard_client):
    """Detail panel must be closeable (ESC key or close button)."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="detail-close"' in html, (
        "Detail panel must contain data-testid=\"detail-close\" — "
        "a close button (ESC handler wired in JS). "
        "Design: detail-panel.jsx close/scrim pattern."
    )


# ---------------------------------------------------------------------------
# AC-S.4.7 — Token hygiene on dashboard template
# ---------------------------------------------------------------------------


def test_index_html_no_bare_hex():
    """index.html must not contain bare hex colour literals outside Jinja/var() contexts."""
    index_path = PROJECT_ROOT / "templates" / "index.html"
    if not index_path.exists():
        pytest.skip("index.html does not exist yet")
    content = index_path.read_text(encoding="utf-8")
    # Strip Jinja comments and var() references
    stripped = re.sub(r"\{#.*?#\}", "", content)
    stripped = re.sub(r"var\([^)]*\)", "", stripped)
    # Negative lookbehind excludes HTML decimal entities (e.g. &#9432; &#x24D8;)
    # whose # is preceded by & — these are not hex colour literals.
    bare_hex = re.findall(r"(?<!&)#[0-9a-fA-F]{3,8}\b", stripped)
    assert bare_hex == [], (
        f"index.html contains bare hex literals: {bare_hex}. "
        "All colours must use var(--studio-*) tokens. "
        "No hardcoded hex outside tokens.css."
    )


def test_index_html_no_tailwind_cdn(dashboard_client):
    """index.html must not load Tailwind CSS from a CDN."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert "cdn.tailwindcss.com" not in html, (
        "Dashboard must not load Tailwind CSS from CDN. "
        "Styling must use only var(--studio-*) tokens from tokens.css. "
        "Tailwind classes conflict with the Studio design system."
    )


def test_index_html_loads_tokens_css(dashboard_client):
    """index.html must load tokens.css."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert "tokens.css" in html, (
        "Dashboard must include tokens.css for --studio-* CSS variables."
    )


def test_index_html_loads_tweaks_js(dashboard_client):
    """index.html must load tweaks.js."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert "tweaks.js" in html, (
        "Dashboard must load tweaks.js for theme/density/accent persistence."
    )


# ---------------------------------------------------------------------------
# AC-S.4.8 — API contract: meta.portfolio fields present
# ---------------------------------------------------------------------------


def test_api_state_meta_portfolio_present():
    """'/api/state' active response must include meta.portfolio with hist_ chart data."""
    import app as app_module
    app_module.app.config["TESTING"] = True
    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_ACTIVE):
        with app_module.app.test_client() as c:
            resp = c.get("/api/state")
    import json
    data = json.loads(resp.data)
    assert "meta" in data, "'/api/state' must include top-level 'meta' key"
    meta = data["meta"]
    assert "portfolio" in meta, (
        "meta must include 'portfolio' object with hist_dates, hist_bot, hist_held "
        "for the Hero cumulative chart."
    )
    portfolio = meta["portfolio"]
    for field in ["hist_dates", "hist_bot", "hist_held", "tc", "tc_if_held",
                  "cr", "cr_if_held", "mdd", "mdd_if_held"]:
        assert field in portfolio, (
            f"meta.portfolio must include '{field}' for Hero chart and VsRow binding. "
            "Design: studio.jsx Hero uses data.meta.portfolio.*"
        )


def test_api_state_meta_portfolio_hist_lists_same_length():
    """meta.portfolio hist_dates, hist_bot, hist_held must be same length."""
    import app as app_module
    app_module.app.config["TESTING"] = True
    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_ACTIVE):
        with app_module.app.test_client() as c:
            resp = c.get("/api/state")
    import json
    data = json.loads(resp.data)
    portfolio = data.get("meta", {}).get("portfolio", {})
    if not all(k in portfolio for k in ["hist_dates", "hist_bot", "hist_held"]):
        pytest.skip("meta.portfolio hist fields not present yet")
    assert len(portfolio["hist_dates"]) == len(portfolio["hist_bot"]) == len(portfolio["hist_held"]), (
        "meta.portfolio hist_dates, hist_bot, hist_held must have equal lengths "
        "for the chart to render correctly."
    )


# ---------------------------------------------------------------------------
# AC-S.4.9 — Dashboard uses chrome include
# ---------------------------------------------------------------------------


def test_dashboard_includes_chrome(dashboard_client):
    """Dashboard must include the Studio chrome nav."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="studio-nav"' in html, (
        "Dashboard must render the Studio chrome nav via {% include '_chrome.html' %}. "
        "data-testid=\"studio-nav\" must be present."
    )


def test_dashboard_active_route_is_dashboard(dashboard_client):
    """Dashboard route must mark the Dashboard nav link as active."""
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'aria-current="page"' in html, (
        "Dashboard route must set aria-current=\"page\" on the active nav link."
    )


# ---------------------------------------------------------------------------
# AC-S.4.10 — No regressions: /api/state still responds
# ---------------------------------------------------------------------------


def test_api_state_still_responds(dashboard_client):
    """/api/state must still return 200 after dashboard changes."""
    resp = dashboard_client.get("/api/state")
    assert resp.status_code == 200, "/api/state must return 200 — no regressions."


def test_api_state_preserves_existing_keys(dashboard_client):
    """/api/state must preserve all existing top-level keys."""
    import json
    resp = dashboard_client.get("/api/state")
    data = json.loads(resp.data)
    for key in ["status", "live_mode", "bot_state", "portfolio_strip"]:
        assert key in data, (
            f"/api/state must preserve existing key '{key}'. "
            "No existing keys may be removed (AC-S.9)."
        )


# ---------------------------------------------------------------------------
# UX review BLOCKs (cycle-2-ux-review.md) — 5 BLOCKs
# ---------------------------------------------------------------------------


def test_triggered_verdict_shows_computed_label(dashboard_client):
    """Triggered card verdict must show computed 'Good call' or 'Early exit', not raw reason.

    ux BLOCK-1: Cards render triggered_reason string directly instead of the
    computed good-call/early-exit verdict derived from guard_alpha.
    Design: studio.jsx triggered card verdict row shows:
      '✓ Good call · saved +X%α'  (guard_alpha > 0)
      '⤓ Early exit · gave up X%α'  (guard_alpha <= 0)
    guard_alpha must be present in the symphony context and used to compute verdict.
    """
    html = dashboard_client.get("/").data.decode("utf-8")
    verdict_elem = re.search(r'data-testid="triggered-verdict"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)
    assert verdict_elem is not None, (
        "data-testid=\"triggered-verdict\" must be present for triggered symphonies."
    )
    verdict_text = verdict_elem.group(0)
    assert ("Good call" in verdict_text or "Early exit" in verdict_text), (
        "triggered-verdict must contain 'Good call' or 'Early exit' computed label. "
        "Raw triggered_reason string is not acceptable. "
        "Derive from guard_alpha: positive = Good call, negative/zero = Early exit."
    )
    assert "%α" in verdict_text or "%" in verdict_text, (
        "triggered-verdict must include a percentage alpha value (e.g. '+3.5%α'). "
        "Design: studio.jsx triggered card verdict."
    )


def test_vs_rows_have_bar_elements(dashboard_client):
    """Each VsRow must contain proportional bar track elements.

    ux BLOCK-2: VsRows render as plain text grid, not proportional bars.
    Design: studio.jsx VsRow has two bar tracks per row — Bot bar (accent colour,
    width proportional to value) and If Held bar (ink-faint, proportional).
    The bar visualisation is the dominant visual element of the hero right column.
    """
    html = dashboard_client.get("/").data.decode("utf-8")
    bars = re.findall(r'data-testid="vs-bar"', html)
    assert len(bars) >= 3, (
        f"Each VsRow must contain at least one data-testid=\"vs-bar\" element "
        f"(proportional bar track). Expected >= 3 (one per row minimum), found {len(bars)}. "
        "Design: studio.jsx VsRow bar tracks."
    )


def test_hero_has_two_column_layout(dashboard_client):
    """Hero section must use a two-column grid layout.

    ux BLOCK-3 (part 1): Hero renders as single column instead of the
    1.15fr/1fr two-column grid from the design.
    Design: studio.jsx Hero grid-template-columns: 1.15fr 1fr —
    left = headline + chart + legend, right = VsRows + mini stats.
    """
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="hero-left"' in html and 'data-testid="hero-right"' in html, (
        "Hero section must have separate data-testid=\"hero-left\" and "
        "data-testid=\"hero-right\" columns in a two-column grid layout. "
        "Design: studio.jsx Hero grid-template-columns: 1.15fr 1fr."
    )


def test_hero_has_mini_stats(dashboard_client):
    """Hero section must contain mini stats (Tracked/Armed/Triggered counts).

    ux BLOCK-3 (part 2): meta.tracked, meta.armed, meta.triggered are built
    by _build_meta() but never rendered in the Hero section.
    Design: studio.jsx Hero right column includes a mini-stats block with
    Tracked / Armed / Triggered counts from meta.
    """
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="mini-stats"' in html, (
        "Hero section must contain data-testid=\"mini-stats\" with "
        "Tracked / Armed / Triggered count items. "
        "Design: studio.jsx Hero right column mini-stats block."
    )
    # At least 3 stat items
    stat_items = re.findall(r'data-testid="mini-stat"', html)
    assert len(stat_items) >= 3, (
        f"mini-stats must contain at least 3 data-testid=\"mini-stat\" items "
        f"(Tracked, Armed, Triggered). Found {len(stat_items)}."
    )


def test_dashboard_has_status_strip(dashboard_client):
    """Dashboard must render status/system information visible to the operator.

    V-40 fix: the separate status-strip bar was removed and status info moved
    inline into the chrome nav right-rail (data-testid="system-online-dot",
    "next-run-ticker"). The contract is that some system status indicator is
    present in the page, not a separate strip element.
    """
    html = dashboard_client.get("/").data.decode("utf-8")
    # After V-40, status info is inline in the chrome nav — assert the
    # system-online-dot or equivalent is present
    has_status = (
        'data-testid="status-strip"' in html
        or 'data-testid="system-online-dot"' in html
        or 'system-online' in html
        or 'next-run' in html
    )
    assert has_status, (
        "Dashboard must render a system status indicator. "
        "After V-40 this is inline in the chrome nav (system-online-dot, next-run-ticker). "
        "Neither a status-strip nor inline status indicators are present."
    )
    # Must contain a market state label
    assert "Market" in html or "market" in html or "ONLINE" in html or "OFFLINE" in html or "CLOSED" in html, (
        "Dashboard must contain market/system state label text. "
        "Bind to meta.market_state_label from _build_meta()."
    )


def test_hero_chart_has_legend(dashboard_client):
    """Cumulative chart must have a legend strip below it.

    ux BLOCK-5: Chart legend strip below the cumulative chart is absent.
    Design: studio.jsx Hero left column — legend strip with:
      - Bot line swatch + 'Planet Stopper (dry-run)' label
      - Dashed held swatch + 'If held' label
      - Right-aligned 'data as of {meta.portfolio.data_as_of}' timestamp
    """
    html = dashboard_client.get("/").data.decode("utf-8")
    assert 'data-testid="chart-legend"' in html, (
        "Hero cumulative chart must be followed by data-testid=\"chart-legend\" "
        "legend strip. "
        "Design: studio.jsx Legend items below CumChart."
    )
    assert "Planet Stopper" in html, (
        "chart-legend must contain 'Planet Stopper' label for the Bot line. "
        "Design: studio.jsx Legend color=p.accent label='Planet Stopper (dry-run)'."
    )
    assert "If held" in html or "if held" in html.lower(), (
        "chart-legend must contain 'If held' label for the dashed baseline. "
        "Design: studio.jsx Legend dashed=true label='If held'."
    )
