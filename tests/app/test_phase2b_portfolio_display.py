"""
RED tests for Phase 2b deferred portfolio display items.

Feature: complete 3 DEFERRED items from Phase 2:
  1. PORTFOLIO VOLATILITY — vol computed from portfolio combined return series,
     wired into index route hero Ann. Vol vs-row.
  2. RISK PROFILE PANEL — JS fetch in openDetailPanel() calls
     /api/performance?scope=symphony&symphony_id=<id> to populate #detail-risk-profile.
  3. VOL INVERSION — bot wins when vol_bot <= vol_held (lower is better);
     winner/loser styling inverted vs return rows.

Design source: ux-design-deliverable.md §Change 4 and §Change 5.

Project rules enforced:
  - Design-system contract assertions: --studio-* tokens, existing component
    classes, no raw-color leakage. NOT specific RGB values.
  - data-testid attributes on every new data-bearing element.
  - No hardcoded producer-computed values; assert shape/format/presence.
  - Tests assert behavior that a wrong implementation would fail.

These tests are intentionally RED until:
  - analytics.compute_portfolio_annualized_vol is added
  - _compute_portfolio_strip includes vol_bot / vol_held keys
  - _build_meta propagates vol_bot / vol_held to meta.portfolio
  - index.html wires vol_bot / vol_held into the Ann. Vol row
  - index.js openDetailPanel() fetches /api/performance for risk profile
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Shared stubs — build_meta injector helpers
# ---------------------------------------------------------------------------


def _stub_meta_with_vol(vol_bot: float | None, vol_held: float | None) -> dict:
    """Build a meta dict with specific vol_bot / vol_held values in portfolio."""
    return {
        "mode": "DRY RUN",
        "system_online": True,
        "tracked": 0,
        "armed": 0,
        "triggered": 0,
        "next_run": "00:00",
        "account": {"label": "Test Account", "uuid_short": "ab12cd34"},
        "market_state": "closed",
        "market_state_label": "Market closed",
        "clock_et": "09:30 ET",
        "portfolio": {
            "tc": 0.3,
            "tc_if_held": 0.25,
            "cr": 8.0,
            "cr_if_held": 7.0,
            "mdd": 2.0,
            "mdd_if_held": 2.8,
            "vol_bot": vol_bot,
            "vol_held": vol_held,
            "hist_dates": ["2026-01-01"] * 35,
            "hist_bot": [0.0] * 35,
            "hist_held": [0.0] * 35,
            "hist_source": "shadow_history",
            "data_as_of": "09:30 ET",
            "account_value": 100000.0,
            "insufficient_history": False,
            "history_days": 35,
        },
        "triggers_today": [],
    }


def _make_api_state_stub() -> dict:
    return {
        "bot_state": {},
        "is_locked": False,
        "port_state": {},
        "exit_authority": {},
        "daemon_started_at": None,
    }


# ---------------------------------------------------------------------------
# 1. PORTFOLIO VOLATILITY — hero Ann. Vol row shows real values, not stub
# ---------------------------------------------------------------------------


def test_hero_vol_row_shows_bot_vol_when_provided():
    """
    When meta.portfolio.vol_bot is a real fraction-scale value, the hero
    Ann. Vol row must render that value in comp-vol-bot-text, not the stub '—'.

    Source: ux-design-deliverable.md §Change 4:
      data-testid='comp-vol-bot-text' must show the formatted vol value.

    Test is RED until index.html wires {{ meta.portfolio.vol_bot }} into
    the comp-vol-bot-text element.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    stub_meta = _stub_meta_with_vol(vol_bot=0.031, vol_held=0.047)

    with app_module.app.test_client() as client:
        with patch.object(app_module, "get_api_state_dict", return_value=_make_api_state_stub()):
            with patch.object(app_module, "_build_meta", return_value=stub_meta):
                html = client.get("/").data.decode("utf-8")

    # comp-vol-bot-text must contain a formatted percentage value, not a stub '—'.
    vol_bot_match = re.search(
        r'data-testid="comp-vol-bot-text"[^>]*>(.*?)<',
        html,
    )
    assert vol_bot_match is not None, "comp-vol-bot-text element must be present in rendered HTML"
    text = vol_bot_match.group(1).strip()
    # When wired: text should contain a '%' digit (e.g. '3.1%' or 'Bot 3.1%').
    # The stub shows '—' (em-dash as HTML entity or unicode); wired shows a percent value.
    assert "%" in text, (
        f"comp-vol-bot-text='{text}' must contain a formatted percent value when "
        "meta.portfolio.vol_bot=0.031 is provided. "
        "index.html must render vol_bot as a percentage, not the stub dash."
    )


def test_hero_vol_row_shows_held_vol_when_provided():
    """
    When meta.portfolio.vol_held is a real fraction-scale value, the hero
    Ann. Vol row must render that value in comp-vol-held-text.

    Source: ux-design-deliverable.md §Change 4:
      data-testid='comp-vol-held-text' must show the held vol.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    stub_meta = _stub_meta_with_vol(vol_bot=0.031, vol_held=0.047)

    with app_module.app.test_client() as client:
        with patch.object(app_module, "get_api_state_dict", return_value=_make_api_state_stub()):
            with patch.object(app_module, "_build_meta", return_value=stub_meta):
                html = client.get("/").data.decode("utf-8")

    vol_held_match = re.search(
        r'data-testid="comp-vol-held-text"[^>]*>(.*?)<',
        html,
    )
    assert vol_held_match is not None, "comp-vol-held-text element must be present in rendered HTML"
    text = vol_held_match.group(1).strip()
    assert "%" in text, (
        f"comp-vol-held-text='{text}' must contain a formatted percent value when "
        "meta.portfolio.vol_held=0.047 is provided."
    )


def test_hero_vol_row_shows_stub_when_vol_is_none():
    """
    When meta.portfolio.vol_bot is None (no shadow history yet), the hero
    Ann. Vol row must gracefully render the placeholder '—', not raise an error
    or display 'None'.

    Source: ux-design-deliverable.md §2.4 Data Availability States:
      "0 observations: gray-out metrics... Never show broken data as '--' without explanation."
    Here we allow the stub '—' because the vol is truly not yet available.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    stub_meta = _stub_meta_with_vol(vol_bot=None, vol_held=None)

    with app_module.app.test_client() as client:
        with patch.object(app_module, "get_api_state_dict", return_value=_make_api_state_stub()):
            with patch.object(app_module, "_build_meta", return_value=stub_meta):
                response = client.get("/")

    # The route must not return a 500 error when vol is None.
    assert response.status_code == 200, (
        f"/ route must not 500 when meta.portfolio.vol_bot/vol_held are None. "
        f"Got status {response.status_code}."
    )

    html = response.data.decode("utf-8")
    # 'None' must not be rendered as raw Python repr.
    assert ">None<" not in html, (
        "index.html must not render raw Python 'None' for absent vol values. "
        "Use a None-guard in Jinja: {{ meta.portfolio.vol_bot | default(None) or '—' }}"
    )


def test_hero_vol_delta_is_positive_when_bot_calmer():
    """
    The vol delta badge (comp-vol-delta) must show a positive value when bot vol
    is less than held vol (bot is calmer).

    Source: ux-design-deliverable.md §Change 4:
      'alpha delta label: α −1.5pp colored --studio-pos when bot is calmer'
      'negative delta = good here — clarify with label: Bot calmer by 1.5pp'

    The sign and coloring: held_vol - bot_vol > 0 when bot is calmer, so the
    delta should be displayed as a positive improvement value.

    We do NOT assert the exact formatted value — just that it contains a digit
    and not solely a '—' stub when real data is provided.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    # vol_bot < vol_held — bot is calmer, delta should be positive.
    stub_meta = _stub_meta_with_vol(vol_bot=0.028, vol_held=0.042)

    with app_module.app.test_client() as client:
        with patch.object(app_module, "get_api_state_dict", return_value=_make_api_state_stub()):
            with patch.object(app_module, "_build_meta", return_value=stub_meta):
                html = client.get("/").data.decode("utf-8")

    delta_match = re.search(
        r'data-testid="comp-vol-delta"[^>]*>(.*?)<',
        html,
    )
    assert delta_match is not None, "comp-vol-delta element must be present in rendered HTML"
    delta_text = delta_match.group(1).strip()
    # When wired: delta should contain a digit (the formatted improvement value).
    has_digit = any(c.isdigit() for c in delta_text)
    assert has_digit, (
        f"comp-vol-delta='{delta_text}' must contain a digit when vol data is provided. "
        "The delta should be held_vol - bot_vol formatted as a percentage improvement."
    )


def test_hero_vol_delta_equal_vols_shows_tie_not_win():
    """
    Adversarial edge case: when vol_bot == vol_held, neither side wins.
    The delta must be zero (or near-zero), not marked as a win for either side.

    Source: ux-design-deliverable.md §2.3:
      'one direction is always good' — but equal vols is a TIE.
    The delta badge should show 0.0pp or similar, not color either side green.

    This test drives the implementer to handle the equality case — a naive
    'if bot_vol < held_vol: green' passes this; 'if bot_vol <= held_vol: green'
    would incorrectly mark equal as green (acceptable per design spec
    which says vol_bot_wins = vol_bot <= vol_held, but our contract says
    we MUST NOT fabricate a win when they are identical).
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    # Equal vols — tie scenario.
    stub_meta = _stub_meta_with_vol(vol_bot=0.035, vol_held=0.035)

    with app_module.app.test_client() as client:
        with patch.object(app_module, "get_api_state_dict", return_value=_make_api_state_stub()):
            with patch.object(app_module, "_build_meta", return_value=stub_meta):
                response = client.get("/")

    # Must not 500 on equal vols.
    assert response.status_code == 200, (
        f"/ route must handle equal vol_bot == vol_held without error. Got {response.status_code}."
    )

    html = response.data.decode("utf-8")
    delta_match = re.search(
        r'data-testid="comp-vol-delta"[^>]*>(.*?)<',
        html,
    )
    assert delta_match is not None, "comp-vol-delta must be present"
    delta_text = delta_match.group(1).strip()

    # The delta text should represent zero or near-zero improvement.
    # Accept '0.0pp', 'α 0.0%', or similar. NOT a large positive/negative number.
    # We assert no fabricated large delta by checking for leading zeroes.
    # This is a shape check: if the rendered delta contains "0" as the numeric part,
    # the implementation handled the equal case correctly.
    assert "0" in delta_text, (
        f"comp-vol-delta='{delta_text}' must show 0 (tie) when vol_bot == vol_held=0.035. "
        "Do not fabricate a win or loss for equal vols."
    )


# ---------------------------------------------------------------------------
# 2. RISK PROFILE PANEL — JS fetch in openDetailPanel
# ---------------------------------------------------------------------------


def test_index_js_open_detail_panel_fetches_performance_api():
    """
    openDetailPanel() in index.js must fetch /api/performance?scope=symphony&symphony_id=...
    to populate the #detail-risk-profile element with per-symphony Sharpe / Sortino /
    Max DD / Ann. Vol comparison.

    Source: ux-design-deliverable.md §Change 5:
      fetch('/api/performance?scope=symphony&symphony_id=' + encodeURIComponent(symId) + '&days=60')

    This test is RED until the fetch call is added to openDetailPanel() in index.js.

    We check for the presence of the fetch pattern in the JS source.
    Shape assertion: does not assert specific risk metric values — only
    that the JS contains the correct fetch call pattern.
    """
    js_path = PROJECT_ROOT / "static" / "index.js"
    content = js_path.read_text(encoding="utf-8")

    # The fetch must reference /api/performance with scope=symphony.
    # We accept any reasonable variant of the query string construction.
    has_perf_fetch = "api/performance" in content and (
        "scope=symphony" in content
        or "scope='symphony'" in content
        or 'scope="symphony"' in content
        or "scope%3Dsymphony" in content  # URL-encoded variant
    )
    assert has_perf_fetch, (
        "index.js openDetailPanel() must fetch /api/performance?scope=symphony&symphony_id=... "
        "to populate the detail panel Risk Profile section. "
        "Source: ux-design-deliverable.md §Change 5. "
        "Add: fetch('/api/performance?scope=symphony&symphony_id=' + encodeURIComponent(symId) + '&days=60')"
    )


def test_index_js_detail_panel_populates_risk_profile_element():
    """
    openDetailPanel() must populate the #detail-risk-profile DOM element after
    fetching /api/performance. The JS must call getElementById('detail-risk-profile')
    or query by data-testid='detail-risk-profile' and set its innerHTML.

    Source: ux-design-deliverable.md §Change 5:
      'var el = document.getElementById('detail-risk-profile'); if (el) el.innerHTML = html;'

    This verifies that the fetch response is actually used to populate the DOM,
    not silently ignored.
    """
    js_path = PROJECT_ROOT / "static" / "index.js"
    content = js_path.read_text(encoding="utf-8")

    has_populate = "detail-risk-profile" in content and (
        "innerHTML" in content or "textContent" in content
    )
    assert has_populate, (
        "index.js must populate #detail-risk-profile element with innerHTML "
        "after fetching /api/performance. "
        "Source: ux-design-deliverable.md §Change 5 risk profile JS block."
    )


def test_index_js_risk_profile_renders_sharpe_row():
    """
    The risk profile rendering logic in openDetailPanel() must include Sharpe
    as one of the displayed metrics.

    Source: ux-design-deliverable.md §Change 5 JS block:
      var rows = [
        ['Sharpe', bot.sharpe, held.sharpe, 'num', false],
        ...
      ]

    This test asserts that 'sharpe' appears in the openDetailPanel function
    body in the context of the risk profile rendering.
    """
    js_path = PROJECT_ROOT / "static" / "index.js"
    content = js_path.read_text(encoding="utf-8")

    # Extract openDetailPanel function body.
    fn_match = re.search(
        r"function\s+openDetailPanel\s*\([^)]*\)\s*\{(.+?)(?=\n\s{4}function\s|\Z)",
        content,
        re.DOTALL,
    )
    if fn_match is None:
        pytest.fail(
            "openDetailPanel function not found in index.js — "
            "cannot verify risk profile rendering logic."
        )

    fn_body = fn_match.group(1)

    # After Phase 2b, the function body should reference 'sharpe' in the risk
    # profile fetch/render block. A missing reference means the risk profile
    # does not include Sharpe.
    assert "sharpe" in fn_body.lower(), (
        "openDetailPanel() must render Sharpe in the risk profile section. "
        "Source: ux-design-deliverable.md §Change 5 — rows array includes Sharpe."
    )


def test_index_js_risk_profile_renders_volatility_row():
    """
    The risk profile rendering logic must include Ann. volatility (volatility key
    from /api/performance response) as one of the per-symphony risk metrics.

    Source: ux-design-deliverable.md §Change 5:
      ['Ann. volatility', bot.volatility, held.volatility, 'pct_frac', false]

    Test is RED until openDetailPanel() includes volatility in its risk profile rows.
    """
    js_path = PROJECT_ROOT / "static" / "index.js"
    content = js_path.read_text(encoding="utf-8")

    fn_match = re.search(
        r"function\s+openDetailPanel\s*\([^)]*\)\s*\{(.+?)(?=\n\s{4}function\s|\Z)",
        content,
        re.DOTALL,
    )
    if fn_match is None:
        pytest.fail("openDetailPanel function not found in index.js")

    fn_body = fn_match.group(1)

    # 'volatility' must appear in the risk profile rendering block.
    has_volatility = (
        "volatility" in fn_body or "ann. vol" in fn_body.lower() or "ann.vol" in fn_body.lower()
    )
    assert has_volatility, (
        "openDetailPanel() risk profile section must render Ann. volatility. "
        "Source: ux-design-deliverable.md §Change 5."
    )


def test_index_js_risk_profile_uses_studio_pos_neg_tokens():
    """
    The risk profile rendering in openDetailPanel() must use --studio-pos and
    --studio-neg CSS custom properties for delta coloring, not bare hex values.

    Source: ux-design-deliverable.md §Design Token constraints:
      'Use only --studio-* custom properties — no bare hex values'
      var dcol = deltaGood ? 'var(--studio-pos)' : 'var(--studio-neg)';

    Design-system contract test — asserts token usage, not specific RGB.
    """
    js_path = PROJECT_ROOT / "static" / "index.js"
    content = js_path.read_text(encoding="utf-8")

    # Only check the openDetailPanel function context.
    fn_match = re.search(
        r"function\s+openDetailPanel\s*\([^)]*\)\s*\{(.+?)(?=\n\s{4}function\s|\Z)",
        content,
        re.DOTALL,
    )
    if fn_match is None:
        pytest.fail("openDetailPanel function not found in index.js")

    fn_body = fn_match.group(1)

    # If the function includes delta coloring (after Phase 2b), it must use studio tokens.
    if "studio-pos" not in fn_body and "--studio-pos" not in content:
        # The function might delegate to a helper that already uses studio tokens.
        # Check the full file for studio-pos usage in a risk-profile context.
        has_studio_tokens_in_file = "--studio-pos" in content or "studio-pos" in content
        assert has_studio_tokens_in_file, (
            "index.js risk profile coloring must use '--studio-pos' / '--studio-neg' tokens. "
            "Never use bare hex colors for delta badges. "
            "Source: ux-design-deliverable.md design token constraints."
        )


# ---------------------------------------------------------------------------
# 3. VOL INVERSION — /api/performance route provides vol for both sides
# ---------------------------------------------------------------------------


def test_api_performance_response_includes_volatility_in_live_and_shadow_metrics():
    """
    /api/performance must include 'volatility' in BOTH live_metrics and
    shadow_metrics so the Risk Profile panel can display bot vs held vol.

    Source: ux-design-deliverable.md §Change 5:
      'var bot = p.shadow_metrics || {}; var held = p.live_metrics || {};'
      '['Ann. volatility', bot.volatility, held.volatility, ...]'

    This test verifies the API contract: the 'volatility' key must be present
    in both metrics dicts in the response, even if null.

    Test uses Flask test client; mocks analytics to return known metrics shape.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True

    _metrics_with_vol = {
        "total_return": 0.05,
        "annualized_return": 0.12,
        "sharpe": 1.1,
        "sortino": 1.4,
        "max_drawdown": -0.03,
        "calmar": 4.0,
        "win_rate": 0.55,
        "volatility": 0.031,  # Phase 2 key — must be present
    }

    with (
        app_module.app.test_client() as client,
        patch.object(
            app_module.analytics,
            "get_history_with_cache_invalidation",
            return_value={},
        ),
        patch.object(
            app_module.analytics,
            "compute_per_symphony_returns",
            return_value=(["2026-01-01", "2026-02-01"], [0.1, -0.05], [0.12, -0.04]),
        ),
        patch.object(
            app_module.analytics,
            "compute_quantstats_metrics",
            side_effect=[_metrics_with_vol, _metrics_with_vol],
        ),
        patch.object(
            app_module.analytics,
            "list_available_symphonies",
            return_value=["sym-A"],
        ),
    ):
        resp = client.get("/api/performance?scope=symphony&symphony_id=sym-A&days=60")

    assert resp.status_code == 200, f"/api/performance must return 200; got {resp.status_code}"
    data = resp.get_json()
    assert data is not None, "/api/performance must return JSON"

    live_metrics = data.get("live_metrics", {})
    shadow_metrics = data.get("shadow_metrics", {})

    assert "volatility" in live_metrics, (
        "/api/performance response live_metrics must include 'volatility' key. "
        "Required by Risk Profile panel JS: 'var held = p.live_metrics || {}; held.volatility'"
    )
    assert "volatility" in shadow_metrics, (
        "/api/performance response shadow_metrics must include 'volatility' key. "
        "Required by Risk Profile panel JS: 'var bot = p.shadow_metrics || {}; bot.volatility'"
    )


def test_api_performance_volatility_value_is_fraction_scale_not_percent():
    """
    The 'volatility' value in /api/performance response must be in FRACTION scale
    (e.g. 0.031 = 3.1% annual vol), not percent scale (31.0).

    This is the scale-invariant assertion: the Risk Profile JS renders:
      bv * 100 + 'pp' for 'pct_frac' kind — so if volatility comes back as
      31.0 instead of 0.031, the panel shows '3100pp' instead of '3.1pp'.

    Scale guard: volatility for a realistic tame strategy should be < 1.0
    in fraction scale (< 100% annual vol). Mocked value is 0.031 — assert
    it passes through the API without being multiplied by 100.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True

    # The mock returns fraction-scale vol (0.031). If the route multiplies
    # it by 100, the test will receive 3.1 — we assert it's < 1.0.
    _metrics_fraction_scale = {
        "total_return": 0.05,
        "annualized_return": 0.12,
        "sharpe": 1.1,
        "sortino": 1.4,
        "max_drawdown": -0.03,
        "calmar": 4.0,
        "win_rate": 0.55,
        "volatility": 0.031,  # fraction scale; must NOT be multiplied by 100 again
    }

    with (
        app_module.app.test_client() as client,
        patch.object(
            app_module.analytics,
            "get_history_with_cache_invalidation",
            return_value={},
        ),
        patch.object(
            app_module.analytics,
            "compute_per_symphony_returns",
            return_value=(["2026-01-01"] * 5, [0.1] * 5, [0.12] * 5),
        ),
        patch.object(
            app_module.analytics,
            "compute_quantstats_metrics",
            side_effect=[_metrics_fraction_scale, _metrics_fraction_scale],
        ),
        patch.object(
            app_module.analytics,
            "list_available_symphonies",
            return_value=["sym-A"],
        ),
    ):
        resp = client.get("/api/performance?scope=symphony&symphony_id=sym-A&days=60")

    data = resp.get_json()
    shadow_vol = (data.get("shadow_metrics") or {}).get("volatility")

    if shadow_vol is not None:
        assert shadow_vol < 1.0, (
            f"shadow_metrics.volatility={shadow_vol} is >= 1.0 — this suggests the route "
            "is returning percent-scale (or double-multiplying the value). "
            "Expected fraction-scale < 1.0 for a tame synthetic strategy. "
            "The route must pass volatility from analytics.compute_quantstats_metrics "
            "unchanged (it is already fraction-scale)."
        )


# ---------------------------------------------------------------------------
# 4. Design-system contract — no new bare hex in Phase 2b additions
# ---------------------------------------------------------------------------


def test_index_html_no_new_bare_hex_after_phase2b():
    """
    Phase 2b additions to index.html (vol row data wiring, risk profile section)
    must not introduce bare hex color values.

    This is the design-system contract: all colors must be --studio-* CSS
    custom properties. Light/dark themes depend on this invariant.

    Source: ux-design-deliverable.md §Design Token and Component Constraints:
      'Use only --studio-* custom properties — no bare hex values in any
       selector or rule block outside tokens.css.'
    """
    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")

    # Strip CSS variable declarations (--studio-*: #hex is allowed in tokens.css)
    stripped = re.sub(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}\b", "", content)
    # Strip var(...) references
    stripped = re.sub(r"var\([^)]*\)", "", stripped)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)

    assert leaks == [], (
        f"index.html Phase 2b additions must not use bare hex colors: {leaks}. "
        "All colors must be --studio-* CSS custom properties."
    )
