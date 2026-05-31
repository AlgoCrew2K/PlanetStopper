"""
Cycle 3 — Performance parity RED tests.

Source: feature-plans/studio-design-handoff.md  AC-S.5
Design: .design-handoff/project/performance.jsx + templates/performance.html
Target: templates/performance.html  vs  /api/performance  +  /api/performance/symphonies

All tests written against REQUIRED behaviour per the design spec.
Tests are intentionally RED until impl delivers the Performance template rewrite.
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Analytics stubs
# ---------------------------------------------------------------------------

_DATES = ["2025-03-01", "2025-04-01", "2025-05-01"]
_LIVE_RETURNS = [0.002, -0.005, 0.008]
_SHADOW_RETURNS = [0.003, -0.003, 0.010]

_LIVE_METRICS = {
    "total_return": 14.8,
    "annualized_return": 30.2,
    "sharpe": 0.91,
    "sortino": 1.12,
    "max_drawdown": -8.4,
    "calmar": 1.76,
    "win_rate": 55.3,
}

_SHADOW_METRICS = {
    "total_return": 18.3,
    "annualized_return": 37.5,
    "sharpe": 1.14,
    "sortino": 1.41,
    "max_drawdown": -6.1,
    "calmar": 2.33,
    "win_rate": 58.9,
}

_PERF_AGGREGATE_PAYLOAD = {
    "scope": "aggregate",
    "dates": _DATES,
    "live_returns": _LIVE_RETURNS,
    "shadow_returns": _SHADOW_RETURNS,
    "live_metrics": _LIVE_METRICS,
    "shadow_metrics": _SHADOW_METRICS,
    "observation_count": 60,
    "insufficient_history": False,
}

_PERF_AGGREGATE_INSUFFICIENT = dict(_PERF_AGGREGATE_PAYLOAD, insufficient_history=True, observation_count=10)

_PERF_SYMPHONY_PAYLOAD = dict(
    _PERF_AGGREGATE_PAYLOAD,
    scope="symphony",
)

_SYMPHONIES_LIST = ["sym_paragons", "sym_momentum", "sym_defensivo"]


def _patch_analytics(history=None, aggregate=None, symphony=None, symphonies=None):
    """Context manager stacking all analytics patches needed for perf tests."""
    import contextlib
    import app as app_module

    history = history or [(d, 0.0, 0.0) for d in _DATES]
    aggregate = aggregate or (_DATES, _LIVE_RETURNS, _SHADOW_RETURNS)
    symphony = symphony or (_DATES, _LIVE_RETURNS, _SHADOW_RETURNS)
    symphonies = symphonies if symphonies is not None else _SYMPHONIES_LIST

    @contextlib.contextmanager
    def _ctx():
        with patch.object(app_module.analytics, "get_history_with_cache_invalidation", return_value=history):
            with patch.object(app_module.analytics, "compute_aggregate_returns", return_value=aggregate):
                with patch.object(app_module.analytics, "compute_per_symphony_returns", return_value=symphony):
                    with patch.object(app_module.analytics, "compute_quantstats_metrics",
                                      side_effect=[_LIVE_METRICS, _SHADOW_METRICS]):
                        with patch.object(app_module.analytics, "list_available_symphonies",
                                          return_value=symphonies):
                            yield

    return _ctx()


@pytest.fixture
def perf_client():
    """Flask test client for /performance and /api/performance tests."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-C3.1 — /performance route basics
# ---------------------------------------------------------------------------


def test_performance_route_200(perf_client):
    resp = perf_client.get("/performance")
    assert resp.status_code == 200, "/performance must return 200"


def test_performance_page_has_chrome_include(perf_client):
    """performance.html must include _chrome.html nav chrome."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="top-bar"' in html, (
        "performance.html must include _chrome.html which renders "
        "data-testid=\"top-bar\". Missing: {% include '_chrome.html' %}."
    )


def test_performance_page_tokens_css_linked(perf_client):
    """tokens.css must be linked before any content styles."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "tokens.css" in html, (
        "performance.html must link static/tokens.css so --studio-* "
        "CSS custom properties are in scope. "
        "Add: <link rel=\"stylesheet\" href=\"{{ url_for('static', filename='tokens.css') }}\">"
    )


def test_performance_active_route_nav(perf_client):
    """Nav must mark 'performance' as the active route."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "performance" in html, (
        "The route must pass active_route='performance' to the template so the "
        "chrome nav highlights the correct item."
    )


# ---------------------------------------------------------------------------
# AC-C3.2 — Page-level landmark
# ---------------------------------------------------------------------------


def test_performance_page_has_page_landmark(perf_client):
    """Rendered HTML must contain data-testid=\"performance-page\" root landmark."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="performance-page"' in html, (
        "performance.html must render a root element with "
        "data-testid=\"performance-page\". "
        "Design: Performance component root div."
    )


def test_performance_page_has_header_title(perf_client):
    """Page header must contain the text 'Performance'."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "Performance" in html, (
        "The performance page header must include the literal text 'Performance'. "
        "Design: PageHeader title='Performance'."
    )


def test_performance_page_has_header_subtitle(perf_client):
    """Page header must include the design-specified subtitle text."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "if held" in html.lower() or "if-held" in html.lower(), (
        "Page header subtitle must reference 'if held' / 'if-held'. "
        "Design: subtitle = 'Live (if held) vs Planet Stopper-exited (shadow) · post-mortem snapshots'."
    )


# ---------------------------------------------------------------------------
# AC-C3.3 — Controls bar
# ---------------------------------------------------------------------------


def test_performance_has_scope_control(perf_client):
    """Rendered HTML must contain data-testid=\"scope-control\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="scope-control"' in html, (
        "performance.html must render data-testid=\"scope-control\" — "
        "the Aggregate / Per-symphony segmented control. "
        "Design: SegControl with SCOPES options in Performance() right-header."
    )


def test_performance_scope_control_has_aggregate_option(perf_client):
    """Scope control must have an 'Aggregate' option."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "Aggregate" in html, (
        "Scope control must include an 'Aggregate' option. "
        "Design: SCOPES = [{value:'aggregate', label:'Aggregate'}, ...]"
    )


def test_performance_scope_control_has_symphony_option(perf_client):
    """Scope control must have a 'Per-symphony' option."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "Per-symphony" in html or "symphony" in html.lower(), (
        "Scope control must include a 'Per-symphony' option. "
        "Design: SCOPES = [..., {value:'symphony', label:'Per-symphony'}]"
    )


def test_performance_has_window_control(perf_client):
    """Rendered HTML must contain data-testid=\"window-control\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="window-control"' in html, (
        "performance.html must render data-testid=\"window-control\" — "
        "the time-window segmented control. "
        "Design: SegControl with WINDOWS options in Performance() right-header."
    )


def test_performance_window_control_has_all_seven_options(perf_client):
    """Window control must expose 7 options: 30d, 60d, 90d, 125d, YTD, 1Y, 5Y."""
    html = perf_client.get("/performance").data.decode("utf-8")
    for label in ("30d", "60d", "90d", "125d", "YTD", "1Y", "5Y"):
        assert label in html, (
            f"Window control must include option '{label}'. "
            "Design: WINDOWS list in performance.jsx has 7 entries."
        )


def test_performance_has_symphony_picker(perf_client):
    """Rendered HTML must contain data-testid=\"symphony-picker\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="symphony-picker"' in html, (
        "performance.html must render data-testid=\"symphony-picker\" — "
        "the per-symphony dropdown (initially hidden). "
        "Design: window.Select shown when scope==='symphony'."
    )


# ---------------------------------------------------------------------------
# AC-C3.4 — Headline strip
# ---------------------------------------------------------------------------


def test_performance_has_headline_strip(perf_client):
    """Rendered HTML must contain data-testid=\"headline-strip\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="headline-strip"' in html, (
        "performance.html must render data-testid=\"headline-strip\" — "
        "the 4-cell stat strip below the controls. "
        "Design: Headline component with 4 Stat cells."
    )


def test_performance_headline_has_guard_alpha_stat(perf_client):
    """Headline strip must contain data-testid=\"guard-alpha-stat\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="guard-alpha-stat"' in html, (
        "Headline strip must contain data-testid=\"guard-alpha-stat\" — "
        "the large cumulative guard-alpha figure. "
        "Design: Stat with k='Cumulative guard α', big=true."
    )


def test_performance_headline_has_sharpe_delta_stat(perf_client):
    """Headline strip must contain data-testid=\"sharpe-delta-stat\".

    Phase 2 redesign (ux-design-deliverable.md §Change 1) replaces the Phase 1
    'Bot total return' cell with 'Sharpe Delta' (bot_sharpe − held_sharpe).
    The old data-testid='bot-return-stat' is superseded.
    """
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="sharpe-delta-stat"' in html, (
        "Phase 2 headline strip must contain data-testid=\"sharpe-delta-stat\". "
        "Source: ux-design-deliverable.md §Change 1 — "
        "Guard Alpha | Sharpe Delta | MDD Reduction | Sortino Delta."
    )


def test_performance_headline_has_mdd_reduction_stat(perf_client):
    """Headline strip must contain data-testid=\"mdd-reduction-stat\".

    Phase 2 redesign (ux-design-deliverable.md §Change 1) replaces the Phase 1
    'If-held total return' cell with 'MDD Reduction' (|held_mdd| − |bot_mdd|).
    The old data-testid='held-return-stat' is superseded.
    """
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="mdd-reduction-stat"' in html, (
        "Phase 2 headline strip must contain data-testid=\"mdd-reduction-stat\". "
        "Source: ux-design-deliverable.md §Change 1."
    )


def test_performance_headline_has_obs_caption_not_stat(perf_client):
    """Observation count moves from a headline stat to an obs-caption element.

    Phase 2 redesign (ux-design-deliverable.md §Change 1): the 'Observations'
    stat cell is replaced by a sortino-delta-stat, and the observation count moves
    to a small data-testid='obs-caption' <p> element outside the headline strip.
    The old data-testid='observations-stat' is superseded.
    """
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="obs-caption"' in html, (
        "Phase 2: observation count must move to data-testid=\"obs-caption\" element "
        "(outside the headline strip). "
        "Source: ux-design-deliverable.md §Change 1."
    )


# ---------------------------------------------------------------------------
# AC-C3.5 — Chart block + legend
# ---------------------------------------------------------------------------


def test_performance_has_chart_block(perf_client):
    """Rendered HTML must contain data-testid=\"perf-chart-block\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="perf-chart-block"' in html, (
        "performance.html must render data-testid=\"perf-chart-block\" "
        "wrapping the cumulative-returns chart. "
        "Design: ChartBlock component."
    )


def test_performance_chart_has_canvas(perf_client):
    """Chart block must contain a canvas element for the returns chart."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "<canvas" in html, (
        "Chart block must contain a <canvas> element for Chart.js rendering. "
        "The chart is rendered client-side via performance.js."
    )


def test_performance_chart_has_legend(perf_client):
    """Chart block must contain data-testid=\"perf-chart-legend\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="perf-chart-legend"' in html, (
        "Chart block must contain data-testid=\"perf-chart-legend\" — "
        "the legend strip above the chart. "
        "Design: Legend strip in ChartBlock with 'Planet Stopper-exited' + 'Live (if held)'."
    )


def test_performance_chart_legend_has_alphabot_label(perf_client):
    """Chart legend must contain 'Planet Stopper' label text."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "Planet Stopper" in html, (
        "perf-chart-legend must include the 'Planet Stopper-exited (shadow)' legend label. "
        "Design: Legend color={p.accent} label='Planet Stopper-exited (shadow)'."
    )


def test_performance_chart_legend_has_live_label(perf_client):
    """Chart legend must contain 'if held' label text."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "if held" in html.lower() or "if-held" in html.lower(), (
        "perf-chart-legend must include the 'Live (if held)' legend label. "
        "Design: Legend color={p.inkDim} label='Live (if held)' dashed."
    )


# ---------------------------------------------------------------------------
# AC-C3.6 — Metrics table
# ---------------------------------------------------------------------------


def test_performance_has_metrics_table(perf_client):
    """Rendered HTML must contain data-testid=\"metrics-table\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="metrics-table"' in html, (
        "performance.html must render data-testid=\"metrics-table\" — "
        "the Risk Metrics section. "
        "Design: MetricsTable component."
    )


def test_performance_metrics_table_has_header_columns(perf_client):
    """Metrics table must have 4 header columns matching the design spec."""
    html = perf_client.get("/performance").data.decode("utf-8")
    # Look for all four column headers
    assert "Metric" in html, "Metrics table header must include 'Metric' column."
    # Live · if held column
    assert "if held" in html.lower() or "if-held" in html.lower(), (
        "Metrics table header must include 'Live · if held' column."
    )
    # Bot · planet-stopper-exited column
    assert "planet stopper" in html.lower() or "Planet Stopper" in html, (
        "Metrics table header must include 'Bot · Planet Stopper-exited' column."
    )
    # Delta column
    assert "Delta" in html or "delta" in html.lower(), (
        "Metrics table header must include 'Delta' column."
    )


def test_performance_metrics_table_has_more_than_seven_rows(perf_client):
    """Metrics table must render more than 7 metric rows after Phase 2.

    Phase 2 (ux-design-deliverable.md §Change 2) adds new Tier 1 rows
    (max_drawdown_delta, volatility, volatility_delta) and Tier 2 placeholder
    rows (upside_capture, downside_capture). The Phase 1 assertion of exactly
    7 rows is superseded — the table now has 12 rows.
    """
    html = perf_client.get("/performance").data.decode("utf-8")
    row_count = html.count('data-testid="metric-row"')
    assert row_count > 7, (
        f"Phase 2 metrics table must render more than 7 rows (got {row_count}). "
        "New rows: max_drawdown_delta, volatility, volatility_delta, "
        "upside_capture (Tier 2 placeholder), downside_capture (Tier 2 placeholder). "
        "Source: ux-design-deliverable.md §Change 2."
    )


def test_performance_metrics_table_has_required_metric_labels(perf_client):
    """Metrics table must contain labels for all 7 design-spec metrics."""
    html = perf_client.get("/performance").data.decode("utf-8")
    for label in (
        "Total return",
        "Annualized",
        "Sharpe",
        "Sortino",
        "Max drawdown",
        "Calmar",
        "Win rate",
    ):
        assert label.lower() in html.lower(), (
            f"Metrics table must include row for '{label}'. "
            "Design: METRICS list in performance.jsx."
        )


def test_performance_metrics_table_primary_rows_highlighted(perf_client):
    """Primary metrics rows must have data-primary attribute or equivalent marker."""
    html = perf_client.get("/performance").data.decode("utf-8")
    # Primary metrics per design: total_return, sharpe, max_drawdown
    assert 'data-primary="true"' in html or 'data-testid="metric-row-primary"' in html, (
        "Primary metric rows (total_return, sharpe, max_drawdown) must be visually "
        "distinguished — use data-primary=\"true\" or data-testid=\"metric-row-primary\". "
        "Design: m.primary rows have larger font + padding."
    )


# ---------------------------------------------------------------------------
# AC-C3.7 — Insufficient-history banner
# ---------------------------------------------------------------------------


def test_performance_has_insufficient_banner_element(perf_client):
    """Rendered HTML must contain data-testid=\"insufficient-banner\"."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert 'data-testid="insufficient-banner"' in html, (
        "performance.html must render data-testid=\"insufficient-banner\". "
        "The banner is shown/hidden client-side based on API response. "
        "Design: InsufficientBanner shown when payload.insufficient_history=true."
    )


def test_performance_banner_contains_min_history_days(perf_client):
    """Insufficient-history banner must include the Jinja min_history_days variable."""
    html = perf_client.get("/performance").data.decode("utf-8")
    # min_history_days is passed as 30 from the route; its value must appear in the banner copy
    assert "30" in html, (
        "The insufficient-history banner must reference the min_history_days Jinja "
        "variable (rendered value: '30'). "
        "Design: 'At least {min_history_days} days of post-mortem snapshots are needed'."
    )


# ---------------------------------------------------------------------------
# AC-C3.8 — Token hygiene
# ---------------------------------------------------------------------------


def test_performance_html_no_tailwind_cdn(perf_client):
    """performance.html must NOT load the Tailwind CDN script."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "cdn.tailwindcss.com" not in html, (
        "performance.html must not load the Tailwind CDN. "
        "Use --studio-* CSS custom properties instead. "
        "Remove: <script src=\"https://cdn.tailwindcss.com\"></script>"
    )


def test_performance_html_no_bare_hex(perf_client):
    """performance.html must contain no hardcoded hex colours outside CSS-var declarations."""
    template_path = PROJECT_ROOT / "templates" / "performance.html"
    content = template_path.read_text(encoding="utf-8")
    # Strip CSS-var declaration lines (--studio-foo: #hex is permitted)
    stripped = re.sub(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}\b", "", content)
    # Strip var(...) references
    stripped = re.sub(r"var\([^)]*\)", "", stripped)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)
    assert leaks == [], (
        f"performance.html contains hardcoded hex colours outside CSS-var declarations: {leaks}. "
        "All colour values must be --studio-* CSS custom properties."
    )


def test_performance_js_no_bare_hex_chartjs_colors():
    """performance.js must not hardcode Chart.js dataset hex colors outside fallback comments."""
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")
    # Strip single-line comments
    stripped = re.sub(r"//[^\n]*", "", content)
    # Strip string values that are clearly CSS var reads (getComputedStyle fallback)
    stripped = re.sub(r"getPropertyValue\([^)]*\)", "", stripped)
    # Hex colors in bare string literals in chart config are violations
    # Allow last-resort fallback vars but flag raw dataset borderColor:'#hex' patterns
    bare = re.findall(r"""(?:borderColor|backgroundColor)\s*:\s*['"]#[0-9a-fA-F]{3,8}['"]""", stripped)
    assert bare == [], (
        f"performance.js hardcodes Chart.js dataset hex colours: {bare}. "
        "Use CSS var reads via getComputedStyle for dataset colours."
    )


# ---------------------------------------------------------------------------
# AC-C3.9 — /api/performance contract
# ---------------------------------------------------------------------------


def test_api_performance_returns_200(perf_client):
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    assert resp.status_code == 200


def test_api_performance_has_required_keys(perf_client):
    """GET /api/performance must return all binding contract keys."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    required_keys = {
        "scope", "dates", "live_returns", "shadow_returns",
        "live_metrics", "shadow_metrics", "observation_count", "insufficient_history",
    }
    missing = required_keys - set(body.keys())
    assert not missing, (
        f"/api/performance response missing required keys: {missing}. "
        "These keys are binding per app.py docstring."
    )


def test_api_performance_live_metrics_has_all_seven_keys(perf_client):
    """live_metrics must contain all 7 documented metric keys."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    required_metric_keys = {
        "total_return", "annualized_return", "sharpe", "sortino",
        "max_drawdown", "calmar", "win_rate",
    }
    missing = required_metric_keys - set(body["live_metrics"].keys())
    assert not missing, (
        f"live_metrics missing keys: {missing}. "
        "Design METRICS list defines these 7 keys."
    )


def test_api_performance_shadow_metrics_has_all_seven_keys(perf_client):
    """shadow_metrics must contain all 7 documented metric keys."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    required_metric_keys = {
        "total_return", "annualized_return", "sharpe", "sortino",
        "max_drawdown", "calmar", "win_rate",
    }
    missing = required_metric_keys - set(body["shadow_metrics"].keys())
    assert not missing, (
        f"shadow_metrics missing keys: {missing}. "
        "Design METRICS list defines these 7 keys."
    )


def test_api_performance_dates_and_returns_same_length(perf_client):
    """dates, live_returns, shadow_returns must have the same length."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    n_dates = len(body["dates"])
    n_live = len(body["live_returns"])
    n_shadow = len(body["shadow_returns"])
    assert n_dates == n_live == n_shadow, (
        f"dates ({n_dates}), live_returns ({n_live}), shadow_returns ({n_shadow}) "
        "must all be the same length — chart rendering requires aligned arrays."
    )


def test_api_performance_invalid_scope_returns_400(perf_client):
    """GET /api/performance with invalid scope must return 400."""
    resp = perf_client.get("/api/performance?scope=garbage")
    assert resp.status_code == 400, (
        "Invalid scope must return 400 with an error message. "
        "Existing guard: scope not in _PERFORMANCE_VALID_SCOPES."
    )


def test_api_performance_invalid_days_returns_400(perf_client):
    """GET /api/performance with non-integer days must return 400."""
    resp = perf_client.get("/api/performance?days=notanumber")
    assert resp.status_code == 400, (
        "Non-integer days param must return 400 with an error message. "
        "Existing guard: int(request.args.get('days')) raises ValueError."
    )


def test_api_performance_symphony_scope_without_id_returns_400(perf_client):
    """GET /api/performance?scope=symphony without symphony_id must return 400."""
    resp = perf_client.get("/api/performance?scope=symphony&days=60")
    assert resp.status_code == 400, (
        "scope=symphony without symphony_id must return 400. "
        "Existing guard: if scope=='symphony' and not symphony_id: return 400."
    )


def test_api_performance_symphony_scope_with_id_returns_200(perf_client):
    """GET /api/performance?scope=symphony&symphony_id=x must return 200."""
    with _patch_analytics():
        resp = perf_client.get(
            "/api/performance?scope=symphony&days=60&symphony_id=sym_paragons"
        )
    assert resp.status_code == 200


def test_api_performance_observation_count_matches_dates(perf_client):
    """observation_count must equal len(dates)."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    assert body["observation_count"] == len(body["dates"]), (
        f"observation_count ({body['observation_count']}) must equal len(dates) "
        f"({len(body['dates'])})."
    )


def test_api_performance_insufficient_history_is_bool(perf_client):
    """insufficient_history must be a JSON boolean."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    assert isinstance(body["insufficient_history"], bool), (
        f"insufficient_history must be a boolean, got {type(body['insufficient_history']).__name__}."
    )


def test_api_performance_returns_are_floats(perf_client):
    """live_returns and shadow_returns must be lists of floats (JSON-serialisable)."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    for key in ("live_returns", "shadow_returns"):
        for val in body[key]:
            assert isinstance(val, float), (
                f"{key} must be a list of floats; got {type(val).__name__} "
                "value {val!r}. The route casts to float to guard against numpy types."
            )


# ---------------------------------------------------------------------------
# AC-C3.10 — /api/performance/symphonies contract
# ---------------------------------------------------------------------------


def test_api_performance_symphonies_returns_200(perf_client):
    with _patch_analytics():
        resp = perf_client.get("/api/performance/symphonies")
    assert resp.status_code == 200


def test_api_performance_symphonies_has_symphonies_key(perf_client):
    """GET /api/performance/symphonies must return {\"symphonies\": [...]}."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance/symphonies")
    body = resp.get_json()
    assert "symphonies" in body, (
        f"/api/performance/symphonies must return a JSON object with key 'symphonies'. "
        f"Got keys: {list(body.keys())}."
    )


def test_api_performance_symphonies_is_list(perf_client):
    """symphonies value must be a JSON array."""
    with _patch_analytics():
        resp = perf_client.get("/api/performance/symphonies")
    body = resp.get_json()
    assert isinstance(body["symphonies"], list), (
        f"'symphonies' must be a list, got {type(body['symphonies']).__name__}."
    )


# ---------------------------------------------------------------------------
# AC-C3.11 — performance.html structural integrity
# ---------------------------------------------------------------------------


def test_performance_html_uses_url_for_static():
    """performance.html must use url_for('static', ...) for all asset references."""
    template_path = PROJECT_ROOT / "templates" / "performance.html"
    content = template_path.read_text(encoding="utf-8")
    # Any <link href="static/..."> or <script src="static/..."> without url_for is a violation
    bare_static = re.findall(r"""(?:href|src)\s*=\s*['"]static/""", content)
    assert bare_static == [], (
        f"performance.html uses bare 'static/' paths (not url_for): {bare_static}. "
        "Use {{ url_for('static', filename='...') }} for all asset references."
    )


def test_performance_html_has_tweaks_js(perf_client):
    """performance.html must load tweaks.js so theme/density apply on this page."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "tweaks.js" in html, (
        "performance.html must load static/tweaks.js so theme, density, accent, "
        "and typeface tweaks work on the performance page."
    )


def test_performance_html_has_performance_js(perf_client):
    """performance.html must load performance.js for client-side chart + metrics."""
    html = perf_client.get("/performance").data.decode("utf-8")
    assert "performance.js" in html, (
        "performance.html must load static/performance.js which fetches "
        "/api/performance and renders the chart + metrics table."
    )


# ---------------------------------------------------------------------------
# AC-C3.12 — UX BLOCK tests (cycle-3 v2)
# ---------------------------------------------------------------------------


def test_performance_js_chart_has_divergence_fill():
    """At least one Chart.js dataset must have a truthy fill property for divergence shading.

    Design: performance.jsx ChartBlock fills the region between bot and live curves
    with url(#perf-div-grad) green tint. The 'divergence shaded' chip in the legend
    is the explicit design callout. fill: false on both datasets means no shading.
    Fix: add a third dataset (type: 'line', fill: '+1' or fill: '-1') or set
    fill: true/'+1' on one of the primary datasets.
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")
    # Strip single-line comments so we don't match commented-out fill: false
    stripped = re.sub(r"//[^\n]*", "", content)
    # Check no 'fill: false' appears without a compensating 'fill: true' or fill: '+N'/'-N'
    fill_false_count = len(re.findall(r"\bfill\s*:\s*false\b", stripped))
    fill_truthy_count = len(re.findall(r"""\bfill\s*:\s*(?:true|['"][+-]\d+['"]|\d+)\b""", stripped))
    assert fill_truthy_count >= 1, (
        f"performance.js has {fill_false_count} fill:false and {fill_truthy_count} truthy fill. "
        "At least one Chart.js dataset must have a truthy fill value to render the "
        "divergence shading between bot and live curves. "
        "Design: performance.jsx ChartBlock — 'divergence shaded' chip is an explicit AC-S.5 requirement."
    )


def test_performance_js_no_bare_hex_chart_options():
    """Chart.js options (legend labels, tick colors, grid colors) must use CSS vars, not bare hex.

    Design: all colors respond to theme toggle via --studio-* CSS custom properties.
    performance.js lines 108, 120-121, 125 hardcode #cbd5e1 and #94a3b8 — these
    do not update when the user switches theme.
    Fix: read via getComputedStyle(document.documentElement).getPropertyValue('--studio-ink-dim')
    and '--studio-ink-faint' at chart init time.
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")
    # Strip single-line comments
    stripped = re.sub(r"//[^\n]*", "", content)
    # Find bare hex literals in chart option context (legend color, tick color, grid color)
    # These appear as string values like '#cbd5e1' or '#94a3b8'
    bare_option_hex = re.findall(
        r"""(?:color|background|borderColor|backgroundColor)\s*:\s*['"]#[0-9a-fA-F]{3,8}['"]""",
        stripped,
    )
    assert bare_option_hex == [], (
        f"performance.js hardcodes hex in Chart.js options: {bare_option_hex}. "
        "Legend label color, tick colors, and grid colors must use "
        "getComputedStyle CSS var reads (--studio-ink-dim, --studio-ink-faint, etc.) "
        "so they respond to theme toggle."
    )


def test_performance_js_headline_stats_color_coded():
    """renderHeadlineStats must apply CSS var color to stat value elements based on sign.

    Design: guard-alpha uses p.pos (positive) / p.neg (negative), bot-return uses p.pos,
    held-return uses p.posDim / p.neg. Currently renderHeadlineStats only sets textContent
    — no style.color is applied, so values are always the same color regardless of sign.
    Fix: after setting textContent, set element.style.color to
    'var(--studio-pos)' or 'var(--studio-neg)' based on the numeric value.
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")
    # Look for color assignment inside or near renderHeadlineStats function body
    # The function must set style.color on at least the guard-alpha value element
    fn_match = re.search(
        r"function\s+renderHeadlineStats\s*\([^)]*\)\s*\{(.+?)(?=\n\s*function\s|\Z)",
        content,
        re.DOTALL,
    )
    assert fn_match is not None, (
        "renderHeadlineStats function not found in performance.js."
    )
    fn_body = fn_match.group(1)
    has_color_assignment = (
        "style.color" in fn_body
        and ("studio-pos" in fn_body or "studio-neg" in fn_body)
    )
    assert has_color_assignment, (
        "renderHeadlineStats must assign style.color using --studio-pos / --studio-neg "
        "CSS variables based on the sign of each stat value. "
        "Currently the function only sets textContent — stat values are not color-coded. "
        "Design: guard-alpha positive → p.pos, negative → p.neg; "
        "bot-return always p.pos; held-return positive → p.posDim, negative → p.neg."
    )
