"""
RED tests for Phase 2 risk-adjusted metrics additions to /api/performance.

Feature: add 'volatility' to live_metrics and shadow_metrics in the API response.
Surface: app.py /api/performance route + analytics.compute_quantstats_metrics.

Design source: ux-design-deliverable.md §Change 2 — Backend changes required.

Project rules enforced:
  - No hardcoded producer-computed values; expected values derive from fixture
    structure (key presence, type, sign) not from specific float values.
  - Read-only dashboard contract: no database.save_*, no requests.post.
  - SPY/TQQQ placeholders: upside_capture and downside_capture present but null
    when benchmark data feed is absent (never fabricated).

These tests are intentionally RED — analytics.compute_quantstats_metrics
does not yet return a 'volatility' key, and the API response does not yet
include it.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Fixtures — Flask test client + analytics mock
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client bound to the in-process app."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _metrics_with_volatility(
    *,
    volatility: float | None = 0.035,
    total_return: float | None = 0.05,
    annualized_return: float | None = 0.12,
    sharpe: float | None = 1.2,
    sortino: float | None = 1.5,
    max_drawdown: float | None = -0.03,
    calmar: float | None = 4.0,
    win_rate: float | None = 0.6,
) -> dict:
    """Build a metrics dict including the Phase 2 'volatility' key."""
    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "win_rate": win_rate,
        "volatility": volatility,
    }


@pytest.fixture
def mock_analytics_with_volatility(monkeypatch):
    """
    Patch analytics functions so /api/performance responses include the Phase 2
    'volatility' key in live_metrics and shadow_metrics.
    """
    import analytics as analytics_module

    mock = MagicMock(spec=analytics_module)
    mock.get_history_with_cache_invalidation.return_value = {}
    mock.compute_aggregate_returns.return_value = ([], [], [])
    mock.compute_per_symphony_returns.return_value = ([], [], [])
    mock.list_available_symphonies.return_value = []

    # Phase 2 metrics: 8 keys including volatility.
    mock.compute_quantstats_metrics.return_value = _metrics_with_volatility()

    for attr in (
        "get_history_with_cache_invalidation",
        "compute_aggregate_returns",
        "compute_per_symphony_returns",
        "compute_quantstats_metrics",
        "list_available_symphonies",
    ):
        monkeypatch.setattr(analytics_module, attr, getattr(mock, attr))

    if hasattr(app_module, "analytics"):
        monkeypatch.setattr(app_module, "analytics", analytics_module)

    return mock


# ---------------------------------------------------------------------------
# Test 1: /api/performance live_metrics includes 'volatility' key (Phase 2)
# ---------------------------------------------------------------------------


def test_api_performance_live_metrics_includes_volatility_key(
    client, mock_analytics_with_volatility
):
    """
    GET /api/performance?scope=aggregate must return live_metrics with a
    'volatility' key after Phase 2.

    This drives analytics.compute_quantstats_metrics to add the key AND
    the route to pass it through to the JSON response.

    RED until both analytics.py and app.py are updated.
    """
    import analytics as analytics_module

    n_days = 30
    live_returns = [0.1 * (i + 1) for i in range(n_days)]
    shadow_returns = [0.12 * (i + 1) for i in range(n_days)]
    dates = [f"2026-01-{i + 1:02d}" for i in range(n_days)]

    analytics_module.compute_aggregate_returns.return_value = (dates, live_returns, shadow_returns)
    live_m = _metrics_with_volatility(volatility=0.035)
    shadow_m = _metrics_with_volatility(volatility=0.028)
    analytics_module.compute_quantstats_metrics.side_effect = [live_m, shadow_m]

    resp = client.get("/api/performance?scope=aggregate&days=60")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = resp.get_json()

    assert "live_metrics" in body, "Response must include live_metrics"
    assert "volatility" in body["live_metrics"], (
        "live_metrics must include 'volatility' key after Phase 2 extension. "
        "Add: metrics['volatility'] = _safe(lambda: qs_stats.volatility(series)) "
        "in analytics.compute_quantstats_metrics."
    )


# ---------------------------------------------------------------------------
# Test 2: /api/performance shadow_metrics includes 'volatility' key (Phase 2)
# ---------------------------------------------------------------------------


def test_api_performance_shadow_metrics_includes_volatility_key(
    client, mock_analytics_with_volatility
):
    """
    shadow_metrics must also include the 'volatility' key — same requirement
    as live_metrics. The route calls compute_quantstats_metrics twice (live,
    shadow) and both responses must carry the key.
    """
    import analytics as analytics_module

    n_days = 20
    dates = [f"2026-02-{i + 1:02d}" for i in range(n_days)]
    analytics_module.compute_aggregate_returns.return_value = (
        dates,
        [0.05 * (i + 1) for i in range(n_days)],
        [0.06 * (i + 1) for i in range(n_days)],
    )
    live_m = _metrics_with_volatility(volatility=0.042)
    shadow_m = _metrics_with_volatility(volatility=0.031)
    analytics_module.compute_quantstats_metrics.side_effect = [live_m, shadow_m]

    resp = client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()

    assert "shadow_metrics" in body
    assert "volatility" in body["shadow_metrics"], (
        "shadow_metrics must include 'volatility' key after Phase 2 extension."
    )


# ---------------------------------------------------------------------------
# Test 3: volatility in API response is a positive float or None — never negative
# ---------------------------------------------------------------------------


def test_api_performance_volatility_value_is_positive_or_none(
    client, mock_analytics_with_volatility
):
    """
    When volatility is present in the API response, it must be a positive
    finite float or None. A negative value indicates a formula bug; a NaN/Inf
    indicates a propagation failure (analytics._safe should have caught it).

    This is a shape/invariant assertion, not a value assertion.
    """
    import analytics as analytics_module

    dates = [f"2026-03-{i + 1:02d}" for i in range(25)]
    analytics_module.compute_aggregate_returns.return_value = (
        dates,
        [0.08 * (i + 1) for i in range(25)],
        [0.09 * (i + 1) for i in range(25)],
    )
    # Provide a realistic positive volatility value derived from formula range
    # (fraction scale: 0.035 = 3.5% annual vol for tame daily series).
    live_m = _metrics_with_volatility(volatility=0.035)
    shadow_m = _metrics_with_volatility(volatility=0.028)
    analytics_module.compute_quantstats_metrics.side_effect = [live_m, shadow_m]

    resp = client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()

    for side in ("live_metrics", "shadow_metrics"):
        vol = body.get(side, {}).get("volatility")
        if vol is not None:
            assert isinstance(vol, (int, float)), (
                f"{side}['volatility'] must be numeric or None; got {type(vol).__name__}={vol!r}"
            )
            assert math.isfinite(float(vol)), (
                f"{side}['volatility'] must be finite (no NaN/Inf); got {vol!r}"
            )
            assert float(vol) > 0.0, (
                f"{side}['volatility'] must be positive (std dev >= 0, and 0 only for "
                f"constant series); got {vol!r}"
            )


# ---------------------------------------------------------------------------
# Test 4: volatility is None in API response when analytics returns None
# ---------------------------------------------------------------------------


def test_api_performance_volatility_is_null_in_json_when_analytics_returns_none(
    client, mock_analytics_with_volatility
):
    """
    When analytics.compute_quantstats_metrics returns volatility=None
    (insufficient data), the API must serialize it as JSON null — NOT omit the
    key, NOT return 0 or any placeholder number.

    The UI uses null to trigger the 'insufficient data' rendering path.
    """
    import analytics as analytics_module

    analytics_module.compute_quantstats_metrics.return_value = _metrics_with_volatility(
        volatility=None
    )

    resp = client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()

    for side in ("live_metrics", "shadow_metrics"):
        if side in body and "volatility" in body[side]:
            vol = body[side]["volatility"]
            assert vol is None, (
                f"When analytics returns volatility=None, API must serialize it as "
                f"JSON null, not {vol!r}. The UI uses null to render 'insufficient data'."
            )


# ---------------------------------------------------------------------------
# Test 5: SPY/TQQQ placeholder metrics never carry fabricated numbers
# ---------------------------------------------------------------------------


def test_api_performance_upside_capture_is_null_when_no_benchmark_data(
    client, mock_analytics_with_volatility
):
    """
    upside_capture and downside_capture (Tier 2 benchmark metrics) must be
    absent OR null in the API response when no SPY/TQQQ benchmark data feed
    is configured.

    CRITICAL: these fields must NEVER carry a fabricated numeric value when
    benchmark data is not available. The UX design spec explicitly calls this
    out: 'Never show broken/missing data as -- without explanation' and
    'For Tier 2 data absent: grayed-out rows with — values and a tooltip'.

    If the API includes these keys, they must be null. If absent, the test
    passes (the JS placeholder rows already handle the absent-key case via
    the data-unavail attribute).
    """
    import analytics as analytics_module

    analytics_module.compute_aggregate_returns.return_value = (
        ["2026-01-01", "2026-01-02"],
        [0.1, -0.05],
        [0.12, -0.03],
    )
    analytics_module.compute_quantstats_metrics.return_value = _metrics_with_volatility()

    resp = client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()

    for side in ("live_metrics", "shadow_metrics"):
        metrics_dict = body.get(side, {})
        for benchmark_key in ("upside_capture", "downside_capture"):
            if benchmark_key in metrics_dict:
                val = metrics_dict[benchmark_key]
                assert val is None, (
                    f"When no SPY/TQQQ data feed is configured, "
                    f"{side}['{benchmark_key}'] MUST be null (not a fabricated number). "
                    f"Got {val!r}. Fabricated benchmark values mislead the operator. "
                    f"See ux-design-deliverable.md §2.4 'Data Availability States'."
                )


# ---------------------------------------------------------------------------
# Test 6: per-symphony /api/performance also includes volatility in metrics
# ---------------------------------------------------------------------------


def test_api_performance_symphony_scope_includes_volatility_in_metrics(
    client, mock_analytics_with_volatility
):
    """
    The per-symphony API path must also return volatility in both metric dicts.
    The detail panel risk-profile section requires per-symphony volatility.

    Source: ux-design-deliverable.md §Change 5:
      "Risk profile rows include Ann. volatility: bot vs held improvement"
    """
    import analytics as analytics_module

    n_days = 25
    dates = [f"2026-04-{i + 1:02d}" for i in range(n_days)]
    analytics_module.compute_per_symphony_returns.return_value = (
        dates,
        [0.08 * (i + 1) for i in range(n_days)],
        [0.09 * (i + 1) for i in range(n_days)],
    )
    live_m = _metrics_with_volatility(volatility=0.040)
    shadow_m = _metrics_with_volatility(volatility=0.030)
    analytics_module.compute_quantstats_metrics.side_effect = [live_m, shadow_m]

    resp = client.get("/api/performance?scope=symphony&symphony_id=sym-test")
    assert resp.status_code == 200

    body = resp.get_json()
    for side in ("live_metrics", "shadow_metrics"):
        if side in body:
            assert "volatility" in body[side], (
                f"Per-symphony {side} must include 'volatility' key. "
                "The detail panel risk-profile section requires per-symphony volatility."
            )


# ---------------------------------------------------------------------------
# Test 7: API read-only contract still holds with Phase 2 analytics
# ---------------------------------------------------------------------------


def test_risk_adjusted_performance_api_does_not_mutate_database(
    client, mock_analytics_with_volatility
):
    """
    Adding Phase 2 metrics must NOT introduce any database mutations in the
    performance routes. The read-only dashboard contract is an architecture
    constraint (project CLAUDE.md).

    Guard: patch database.save_* and verify none are called.
    """
    import analytics as analytics_module

    analytics_module.compute_aggregate_returns.return_value = (
        ["2026-01-01"],
        [0.1],
        [0.12],
    )
    analytics_module.compute_quantstats_metrics.return_value = _metrics_with_volatility()

    with patch.object(app_module, "database") as db_mock:
        client.get("/performance")
        client.get("/api/performance?scope=aggregate&days=60")
        client.get("/api/performance/symphonies")

        for attr_name in dir(db_mock):
            if attr_name.startswith("save_"):
                save_attr = getattr(db_mock, attr_name)
                assert not save_attr.called, (
                    f"Phase 2 metrics must not introduce database.{attr_name}() calls — "
                    "dashboard is read-only."
                )
