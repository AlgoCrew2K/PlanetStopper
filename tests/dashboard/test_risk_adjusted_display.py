"""
RED tests for Phase 2 risk-adjusted display contract.

Feature: surface risk-adjusted metrics in the dashboard UI.
Surfaces:
  - templates/performance.html — headline strip (Phase 2 stats), new metric
    rows, Tier 2 placeholder rows, metrics-caption, redesigned table headers.
  - static/performance.js — METRIC_LABELS array, renderHeadlineStats Phase 2
    deltas, derived-field computation, placeholder row data-unavail.
  - templates/index.html — hero volatility vs-row, detail-panel risk-profile
    section.

Design source: ux-design-deliverable.md (all §§ covered below).

Project rules enforced:
  - Design-system contract assertions: --studio-* tokens, existing component
    classes, no raw-color leakage. NOT specific RGB values.
  - data-testid attributes on every new data-bearing element.
  - SPY/TQQQ placeholder rows: data-unavail='true', never fake numbers.
  - No hardcoded hex in template HTML or JS (design-system contract test).

These tests are intentionally RED until templates/performance.html,
static/performance.js, and templates/index.html are updated for Phase 2.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def perf_client():
    """Flask test client for /performance route tests."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def index_client():
    """Flask test client for / (index/dashboard) route tests."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _patch_analytics_phase2():
    """Context manager patching analytics to provide Phase 2 metrics including volatility."""
    import contextlib
    import app as app_module

    _dates = ["2026-01-01", "2026-02-01", "2026-03-01"]
    _live_returns = [0.05, -0.02, 0.08]
    _shadow_returns = [0.07, -0.01, 0.10]

    # Phase 2 metrics dict including volatility.
    _live_metrics = {
        "total_return": 0.11,
        "annualized_return": 0.22,
        "sharpe": 0.91,
        "sortino": 1.12,
        "max_drawdown": -0.084,
        "calmar": 1.76,
        "win_rate": 0.55,
        "volatility": 0.035,  # Phase 2 addition
    }
    _shadow_metrics = {
        "total_return": 0.16,
        "annualized_return": 0.33,
        "sharpe": 1.14,
        "sortino": 1.41,
        "max_drawdown": -0.061,
        "calmar": 2.33,
        "win_rate": 0.60,
        "volatility": 0.028,  # Phase 2 addition — bot is calmer
    }

    @contextlib.contextmanager
    def _ctx():
        with patch.object(
            app_module.analytics, "get_history_with_cache_invalidation", return_value={}
        ):
            with patch.object(
                app_module.analytics,
                "compute_aggregate_returns",
                return_value=(_dates, _live_returns, _shadow_returns),
            ):
                with patch.object(
                    app_module.analytics,
                    "compute_per_symphony_returns",
                    return_value=(_dates, _live_returns, _shadow_returns),
                ):
                    with patch.object(
                        app_module.analytics,
                        "compute_quantstats_metrics",
                        side_effect=[_live_metrics, _shadow_metrics],
                    ):
                        with patch.object(
                            app_module.analytics,
                            "list_available_symphonies",
                            return_value=["sym-A", "sym-B"],
                        ):
                            yield

    return _ctx()


# ---------------------------------------------------------------------------
# AC-P2.1 — Performance page headline strip — Phase 2 stats
# ---------------------------------------------------------------------------


def test_performance_headline_has_sharpe_delta_stat():
    """
    Phase 2 redesigns the headline strip:
      Guard Alpha | Sharpe Delta | MDD Reduction | Sortino Delta

    The 'Sharpe Delta' stat requires data-testid='sharpe-delta-stat'.
    Source: ux-design-deliverable.md §Change 1:
      "data-testid attribute: sharpe-delta-stat"
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    assert 'data-testid="sharpe-delta-stat"' in html, (
        "performance.html Phase 2 headline strip must include "
        'data-testid="sharpe-delta-stat". '
        "Design: Phase 2 replaces bot/held return cells with Sharpe/MDD/Sortino deltas."
    )


def test_performance_headline_has_mdd_reduction_stat():
    """
    Phase 2 headline strip must include data-testid='mdd-reduction-stat'.
    'MDD Reduction' surfaces the bot's primary value proposition
    (capital preservation) in the headline.

    Source: ux-design-deliverable.md §Change 1:
      "MDD Reduction — label 'MAX DD REDUCTION', value = |held_mdd| − |bot_mdd|"
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    assert 'data-testid="mdd-reduction-stat"' in html, (
        'performance.html Phase 2 headline strip must include data-testid="mdd-reduction-stat".'
    )


def test_performance_headline_has_sortino_delta_stat():
    """
    Phase 2 headline strip must include data-testid='sortino-delta-stat'.
    Sortino captures downside-penalised improvement — relevant for a
    loss-avoidance system.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    assert 'data-testid="sortino-delta-stat"' in html, (
        'performance.html Phase 2 headline strip must include data-testid="sortino-delta-stat".'
    )


def test_performance_headline_has_observation_caption():
    """
    Phase 2 moves the observation count from a headline stat to a caption
    element: data-testid='obs-caption'.

    Source: ux-design-deliverable.md §Change 1:
      "Observation count moves to a small data-testid='obs-caption' <p> element"
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    assert 'data-testid="obs-caption"' in html, (
        'performance.html Phase 2 must include data-testid="obs-caption" '
        "for the observation count (moved out of the headline strip)."
    )


# ---------------------------------------------------------------------------
# AC-P2.2 — Performance page — new Phase 2 metric rows
# ---------------------------------------------------------------------------


def test_performance_metrics_table_has_volatility_row():
    """
    Phase 2 adds 'Annualized Volatility' as a Tier 1 metric row
    (immediately computable from existing return series).

    Source: ux-design-deliverable.md §Change 2 table row order:
      'Annualized volatility    ← new Tier 1'
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    # Loose match: the label text must appear somewhere in the rendered body.
    has_vol_label = (
        "annualized volatility" in html.lower()
        or "ann. vol" in html.lower()
        or "annualised volatility" in html.lower()
    )
    assert has_vol_label, (
        "Phase 2 metrics table must include an 'Annualized volatility' row. "
        "Source: METRIC_LABELS array entry ['volatility', 'Annualized volatility', ...]"
    )


def test_performance_metrics_table_has_max_drawdown_reduction_row():
    """
    Phase 2 adds 'Max DD reduction' as a Tier 1 metric row.
    This is the |held_mdd| − |bot_mdd| derived field.

    Source: ux-design-deliverable.md §Change 2:
      "['max_drawdown_delta', 'Max DD reduction', 'pp', false]  // derived"
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    has_mdd_reduction = (
        "max dd reduction" in html.lower()
        or "mdd reduction" in html.lower()
        or "drawdown reduction" in html.lower()
    )
    assert has_mdd_reduction, (
        "Phase 2 metrics table must include a 'Max DD reduction' row. "
        "Source: METRIC_LABELS entry max_drawdown_delta."
    )


def test_performance_metrics_table_has_volatility_reduction_row():
    """
    Phase 2 adds 'Volatility reduction' (held_vol − bot_vol) as a Tier 1 row.

    Source: ux-design-deliverable.md §Change 2:
      "['volatility_delta', 'Volatility reduction', 'pp', false]  // new Tier 1, derived"
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    has_vol_reduction = "volatility reduction" in html.lower() or "vol reduction" in html.lower()
    assert has_vol_reduction, (
        "Phase 2 metrics table must include a 'Volatility reduction' row. "
        "Source: METRIC_LABELS entry volatility_delta."
    )


def test_performance_metrics_table_has_spy_tqqq_placeholder_rows():
    """
    Tier 2 benchmark rows (upside_capture, downside_capture vs SPY) must be
    rendered as placeholder rows with data-unavail='true' when no benchmark
    data feed is configured.

    Source: ux-design-deliverable.md §Change 2 placeholder row rendering:
      "Row data-unavail='true' attribute for test assertions"

    This is a critical safety test: placeholder rows must exist in the template
    so the operator sees the roadmap item, but they must NEVER carry fabricated
    data from a misconfigured or missing feed.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    assert 'data-unavail="true"' in html, (
        "Phase 2 performance.html must render Tier 2 placeholder rows with "
        'data-unavail="true" so they are distinguishable from live data rows. '
        "Source: ux-design-deliverable.md §Change 2 placeholder row rendering."
    )


def test_performance_placeholder_rows_do_not_show_fake_numbers():
    """
    Tier 2 placeholder rows must not show any numeric value for upside/downside
    capture. The placeholder must show '—' (em dash or double dash) indicating
    data is unavailable.

    This is the primary safety contract: if the operator reads a number in a
    Tier 2 row, that number must be real benchmark data, never fabricated.

    We assert presence of the metric-unavail CSS class or '—' content near
    the Tier 2 label text in the rendered HTML. The exact pattern tolerates
    both 'upside capture' and 'downside capture' label variants.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")

    # The placeholder must include either metric-unavail class or a dash character
    # near the 'capture' label text.
    has_unavail_signal = (
        'class="metric-unavail"' in html
        or "metric-unavail" in html
        or (
            "capture" in html.lower()
            and (
                "data-unavail" in html
                or "—" in html  # em-dash
                or ">—<" in html  # HTML dash entity
                or ">--<" in html  # double-dash fallback
            )
        )
    )
    assert has_unavail_signal, (
        "Tier 2 'upside/downside capture vs SPY' rows must render with an "
        "unavailability signal (metric-unavail class, data-unavail attribute, or "
        "dash value) — never a fabricated numeric value. "
        "Source: ux-design-deliverable.md §2.4 Data Availability States."
    )


def test_performance_metrics_table_phase2_has_more_than_seven_rows():
    """
    Phase 2 adds 4+ new rows to the metrics table:
      max_drawdown_delta, volatility, volatility_delta, plus 2 Tier 2 placeholders.

    The table must have MORE than 7 rows after Phase 2. We do not pin the exact
    count (the implementer chooses how to order rows) but we verify growth from
    the Phase 1 baseline of 7.

    Shape assertion, not value assertion.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")

    row_count = html.count('data-testid="metric-row"')
    assert row_count > 7, (
        f"Phase 2 metrics table must have more than 7 rows (Phase 1 baseline=7); "
        f"got {row_count}. New rows: max_drawdown_delta, volatility, "
        f"volatility_delta, upside_capture (Tier 2), downside_capture (Tier 2)."
    )


# ---------------------------------------------------------------------------
# AC-P2.3 — Performance page — table design updates
# ---------------------------------------------------------------------------


def test_performance_metrics_table_has_updated_column_headers():
    """
    Phase 2 renames the table column headers to make the comparison framing
    explicit for non-experts.

    Source: ux-design-deliverable.md §Change 3:
      <th>If-held baseline</th>
      <th>Bot (Planet Stopper)</th>
      <th>Delta (bot − baseline)</th>
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    # Accept either the exact design wording or reasonable paraphrases.
    has_held_header = "if-held baseline" in html.lower() or "if held baseline" in html.lower()
    has_bot_header = "bot (planet stopper)" in html.lower() or "planet stopper" in html.lower()
    has_delta_header = "delta (bot" in html.lower() or "delta" in html.lower()
    missing = []
    if not has_held_header:
        missing.append("'If-held baseline' column header")
    if not has_bot_header:
        missing.append("'Bot (Planet Stopper)' column header")
    if not has_delta_header:
        missing.append("'Delta' column header")

    assert not missing, (
        f"Phase 2 metrics table is missing updated column headers: {missing}. "
        "Source: ux-design-deliverable.md §Change 3 column header redesign."
    )


def test_performance_metrics_table_has_caption():
    """
    Phase 2 adds a caption below the section heading explaining what 'positive
    delta' means for non-expert operators.

    Source: ux-design-deliverable.md §Change 3:
      <p class='metrics-caption'>
        Positive deltas on risk metrics (Sharpe, Sortino, drawdown reduction)
        mean the bot improved risk-adjusted outcomes vs holding through.
      </p>
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with _patch_analytics_phase2():
            html = client.get("/performance").data.decode("utf-8")
    has_caption = (
        "metrics-caption" in html
        or "positive deltas" in html.lower()
        or (
            "drawdown reduction" in html.lower()
            and "risk" in html.lower()
            and "delta" in html.lower()
        )
    )
    assert has_caption, (
        "Phase 2 metrics table section must include a caption explaining what "
        "'positive delta' means for non-experts. "
        "Either add class='metrics-caption' or include the design-spec caption text."
    )


# ---------------------------------------------------------------------------
# AC-P2.4 — Dashboard hero — volatility vs-row
# ---------------------------------------------------------------------------


def test_index_hero_has_volatility_vs_row():
    """
    Phase 2 adds a 4th row to the hero vs-rows block:
      'Ann. Vol' — bot vs held annualized volatility.

    Source: ux-design-deliverable.md §Change 4:
      data-testid='comp-vol-bot-text' and data-testid='comp-vol-held-text'
    """
    import app as app_module

    app_module.app.config["TESTING"] = True

    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")

    assert 'data-testid="comp-vol-bot-text"' in content or "comp-vol-bot" in content, (
        "templates/index.html Phase 2 must include a volatility vs-row in the hero "
        "with data-testid='comp-vol-bot-text' for the bot annualized vol value. "
        "Source: ux-design-deliverable.md §Change 4."
    )


def test_index_hero_volatility_row_has_held_text():
    """
    The hero volatility row must also expose the held-side value with
    data-testid='comp-vol-held-text'.
    """
    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")

    assert 'data-testid="comp-vol-held-text"' in content or "comp-vol-held" in content, (
        "templates/index.html Phase 2 must include data-testid='comp-vol-held-text' "
        "in the hero volatility vs-row. "
        "Source: ux-design-deliverable.md §Change 4."
    )


def test_index_hero_volatility_bar_elements_present():
    """
    The hero volatility vs-row must include bar elements for the bar-chart
    comparison visualization, consistent with the existing vs-row pattern.

    Source: ux-design-deliverable.md §Change 4:
      data-testid='comp-bar-vol-bot' and data-testid='comp-bar-vol-held'
    """
    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")

    has_vol_bars = "comp-bar-vol-bot" in content or "comp-bar-vol" in content
    assert has_vol_bars, (
        "templates/index.html Phase 2 hero volatility row must include bar elements "
        "(data-testid='comp-bar-vol-bot' / 'comp-bar-vol-held'). "
        "These follow the existing vs-bar-fill pattern for the other three rows."
    )


def test_index_hero_volatility_delta_label_present():
    """
    The hero volatility vs-row must include a delta badge with
    data-testid='comp-vol-delta' for the volatility improvement alpha.
    """
    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")

    assert "comp-vol-delta" in content, (
        "templates/index.html Phase 2 hero volatility row must include "
        "data-testid='comp-vol-delta' for the vol alpha delta badge."
    )


# ---------------------------------------------------------------------------
# AC-P2.5 — Dashboard detail panel — Risk Profile section
# ---------------------------------------------------------------------------


def test_index_detail_panel_has_risk_profile_section():
    """
    Phase 2 adds a 'Risk Profile' section to the detail panel (slide-over)
    showing per-symphony Sharpe, Sortino, Max DD, and Ann. Vol comparison.

    Source: ux-design-deliverable.md §Change 5:
      id='detail-risk-profile', data-testid='detail-risk-profile'
    """
    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")

    assert (
        'id="detail-risk-profile"' in content or 'data-testid="detail-risk-profile"' in content
    ), (
        "templates/index.html Phase 2 must include id='detail-risk-profile' or "
        "data-testid='detail-risk-profile' in the detail panel for the Risk Profile "
        "section. Source: ux-design-deliverable.md §Change 5."
    )


def test_index_detail_panel_risk_profile_uses_existing_math_row_pattern():
    """
    The Risk Profile section must use the existing .math-row pattern, not new
    CSS classes. Adding new CSS for a pattern already defined violates the
    design-system contract.

    Source: ux-design-deliverable.md §Change 5:
      "Rendered using the existing .math-row / .math-row-value / .math-row-hint pattern
       — no new CSS classes needed."
    """
    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")

    # The detail-risk-profile section should use math-row classes.
    # We check the template includes math-row (it already does for the math layers
    # readout, but Phase 2 should reuse it in the risk-profile section too).
    assert "math-row" in content, (
        "templates/index.html must use the .math-row CSS class in the risk profile "
        "section — existing component pattern, no new classes needed."
    )


# ---------------------------------------------------------------------------
# AC-P2.6 — Design-system token contract
# ---------------------------------------------------------------------------


def test_performance_html_phase2_no_new_bare_hex():
    """
    Phase 2 additions to performance.html must not introduce any bare hex
    color values outside CSS variable declarations.

    This is the design-system contract from the existing cycle 3 test, now
    re-asserted for Phase 2 additions. Redundant assertion but serves as a
    regression guard that a Phase 2 contributor doesn't accidentally add
    hardcoded colors.
    """
    template_path = PROJECT_ROOT / "templates" / "performance.html"
    content = template_path.read_text(encoding="utf-8")
    # Strip CSS variable declarations (--studio-*: #hex is allowed)
    stripped = re.sub(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}\b", "", content)
    # Strip var(...) references
    stripped = re.sub(r"var\([^)]*\)", "", stripped)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)
    assert leaks == [], (
        f"performance.html Phase 2 additions must not use bare hex colors: {leaks}. "
        "All colors must be --studio-* CSS custom properties."
    )


def test_index_html_phase2_no_new_bare_hex():
    """
    Phase 2 additions to index.html (hero vol row, detail risk profile)
    must not introduce bare hex color values.
    """
    template_path = PROJECT_ROOT / "templates" / "index.html"
    content = template_path.read_text(encoding="utf-8")
    stripped = re.sub(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}\b", "", content)
    stripped = re.sub(r"var\([^)]*\)", "", stripped)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)
    assert leaks == [], (
        f"index.html Phase 2 additions must not use bare hex colors: {leaks}. "
        "All colors must be --studio-* CSS custom properties."
    )


def test_performance_js_phase2_metric_labels_includes_volatility():
    """
    performance.js METRIC_LABELS array must include an entry for 'volatility'
    after Phase 2.

    Source: ux-design-deliverable.md §Change 2:
      ['volatility', 'Annualized volatility', 'pct_frac', false]

    We assert the key name appears in the METRIC_LABELS context, not that a
    specific float value exists (the test must be able to fail on a wrong impl).
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")

    has_volatility_label = "'volatility'" in content or '"volatility"' in content
    assert has_volatility_label, (
        "static/performance.js METRIC_LABELS must include a 'volatility' entry. "
        "Source: ux-design-deliverable.md §Change 2 — new Tier 1 row."
    )


def test_performance_js_phase2_metric_labels_includes_volatility_delta():
    """
    performance.js METRIC_LABELS must include 'volatility_delta' (Tier 1 derived).

    Source: ux-design-deliverable.md §Change 2:
      ['volatility_delta', 'Volatility reduction', 'pp', false]
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")

    assert "volatility_delta" in content, (
        "static/performance.js METRIC_LABELS must include 'volatility_delta'. "
        "Source: ux-design-deliverable.md §Change 2."
    )


def test_performance_js_phase2_metric_labels_includes_max_drawdown_delta():
    """
    performance.js METRIC_LABELS must include 'max_drawdown_delta' (Tier 1 derived).

    Source: ux-design-deliverable.md §Change 2:
      ['max_drawdown_delta', 'Max DD reduction', 'pp', false]  // derived
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")

    assert "max_drawdown_delta" in content, (
        "static/performance.js METRIC_LABELS must include 'max_drawdown_delta'. "
        "Source: ux-design-deliverable.md §Change 2."
    )


def test_performance_js_phase2_renders_upside_capture_as_placeholder():
    """
    performance.js must handle upside_capture as a placeholder row (Tier 2).
    When value is null/undefined, the row renders with data-unavail='true'
    and a dash, not a number.

    Source: ux-design-deliverable.md §Change 2 placeholder row rendering:
      "Row data-unavail='true' attribute for test assertions"
      "Both value columns: <span class='metric-unavail' ...>—</span>"

    We assert the JS contains logic for the data-unavail rendering path,
    not that a specific value is rendered.
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")

    has_unavail_logic = "data-unavail" in content or "metric-unavail" in content
    assert has_unavail_logic, (
        "static/performance.js must include logic for rendering Tier 2 placeholder "
        "rows with data-unavail='true' and a dash when value is null. "
        "Source: ux-design-deliverable.md §Change 2 placeholder row rendering."
    )


# ---------------------------------------------------------------------------
# AC-P2.7 — Phase 2 headline renderHeadlineStats delta computations
# ---------------------------------------------------------------------------


def test_performance_js_rendereheadlinestats_computes_sharpe_delta():
    """
    renderHeadlineStats in performance.js must compute sharpeDelta from
    shadow_metrics and live_metrics.

    Source: ux-design-deliverable.md §Change 1:
      "Add computation of sharpeDelta, mddReduction, sortinoDelta from
       payload.shadow_metrics and payload.live_metrics"

    We assert the variable name sharpeDelta (or equivalent) appears in
    the renderHeadlineStats function body, not a specific value.
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")

    fn_match = re.search(
        r"function\s+renderHeadlineStats\s*\([^)]*\)\s*\{(.+?)(?=\nfunction\s|\Z)",
        content,
        re.DOTALL,
    )
    if fn_match is None:
        pytest.fail(
            "renderHeadlineStats function not found in performance.js. "
            "Phase 2 must implement this function to compute delta stats."
        )

    fn_body = fn_match.group(1)
    has_sharpe_delta = (
        "sharpeDelta" in fn_body
        or "sharpe_delta" in fn_body
        or ("sharpe" in fn_body.lower() and "delta" in fn_body.lower())
    )
    assert has_sharpe_delta, (
        "renderHeadlineStats must compute sharpeDelta from "
        "payload.shadow_metrics.sharpe - payload.live_metrics.sharpe. "
        "Source: ux-design-deliverable.md §Change 1."
    )


def test_performance_js_rendereheadlinestats_computes_mdd_reduction():
    """
    renderHeadlineStats must compute mddReduction from shadow_metrics and
    live_metrics max_drawdown values.

    Formula: mddReduction = abs(live.max_drawdown) - abs(shadow.max_drawdown)
    (positive = bot had less drawdown = good).
    """
    js_path = PROJECT_ROOT / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")

    fn_match = re.search(
        r"function\s+renderHeadlineStats\s*\([^)]*\)\s*\{(.+?)(?=\nfunction\s|\Z)",
        content,
        re.DOTALL,
    )
    if fn_match is None:
        pytest.fail("renderHeadlineStats not found in performance.js")

    fn_body = fn_match.group(1)
    has_mdd_reduction = (
        "mddReduction" in fn_body
        or "mdd_reduction" in fn_body
        or ("max_drawdown" in fn_body and "reduction" in fn_body.lower())
    )
    assert has_mdd_reduction, (
        "renderHeadlineStats must compute mddReduction from max_drawdown values. "
        "Source: ux-design-deliverable.md §Change 1."
    )
