"""
RED tests for the DV2 Performance tab — routes + template binding.

This module pins the dashboard contract for the new Performance tab introduced
in cycle DV2:

  * ``GET /performance``               -> renders templates/performance.html
  * ``GET /api/performance``           -> JSON time-series + quantstats metrics
  * ``GET /api/performance/symphonies`` -> JSON list of available symphony ids

These routes consume the analytics.py data layer (DV1).  The tests patch the
analytics surface (``analytics.get_history_with_cache_invalidation``,
``analytics.compute_aggregate_returns``, ``analytics.compute_per_symphony_returns``,
``analytics.compute_quantstats_metrics``, ``analytics.list_available_symphonies``)
so the route logic is exercised without touching on-disk post_mortem fixtures.

Read-only contract — Performance routes MUST be pure read paths:
they must never call ``database.save_*``, mutate ``bot_state``,
``requests.post`` to a live API, or call ``database.acquire_lock()`` (the live
engine holds that lock at the top of every minute; blocking the dashboard on it
would couple operator UI latency to engine cycle time).

No producer-computed values are hardcoded — every expected value is derived
from the fixture the test itself constructs (see project rule
``feedback_no_hardcoded_test_values``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app as app_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client bound to the in-process app instance (no port bound)."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_analytics(monkeypatch):
    """
    Patch the analytics module surface that the Performance routes consume.

    Tests configure return values per call.  We patch the names *as the route
    will import them* — both ``analytics`` referenced via ``app.analytics`` (if
    the implementer imports the module at the top of app.py) and the raw module
    attributes (covering ``from analytics import ...`` imports too).
    """
    import analytics as analytics_module

    mock = MagicMock(spec=analytics_module)
    # Sensible defaults — individual tests override these.
    mock.get_history_with_cache_invalidation.return_value = {}
    mock.compute_aggregate_returns.return_value = ([], [], [])
    mock.compute_per_symphony_returns.return_value = ([], [], [])
    mock.compute_quantstats_metrics.return_value = {
        "total_return": None,
        "annualized_return": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "calmar": None,
        "win_rate": None,
    }
    mock.list_available_symphonies.return_value = []

    # Patch the module attribute directly (covers both `import analytics` and
    # `from analytics import X` usage in app.py once DV2 lands).
    for attr in (
        "get_history_with_cache_invalidation",
        "compute_aggregate_returns",
        "compute_per_symphony_returns",
        "compute_quantstats_metrics",
        "list_available_symphonies",
    ):
        monkeypatch.setattr(analytics_module, attr, getattr(mock, attr))

    # Also expose the bound module on app_module if the implementer attaches it.
    if hasattr(app_module, "analytics"):
        monkeypatch.setattr(app_module, "analytics", analytics_module)

    return mock


def _build_history_fixture(n_days: int, symphonies: list[str]) -> dict:
    """
    Build a history dict of shape {date: {symphony_id: {...}}} with `n_days`
    chronological dates and one entry per symphony per day.

    All numeric values are deterministic functions of (day_index, sym_index)
    so the test can re-derive them when asserting.
    """
    history: dict = {}
    for i in range(n_days):
        # ISO date strings — month sweeps so 30 days won't roll across
        # year boundaries (we cap by month/day construction).
        date_str = f"2026-{((i // 28) % 12) + 1:02d}-{(i % 28) + 1:02d}"
        day_entries: dict = {}
        for j, sym in enumerate(symphonies):
            day_entries[sym] = {
                "live_ret": 0.001 * (i + 1) + 0.0001 * j,
                "f_ret": 0.002 * (i + 1) + 0.0001 * j,
                "value": 1000.0 + 10.0 * i + j,
            }
        history[date_str] = day_entries
    return history


# ---------------------------------------------------------------------------
# 1. GET /performance — template rendering
# ---------------------------------------------------------------------------


def test_get_performance_returns_200_html(client, mock_analytics):
    """
    GET /performance must return 200 with an HTML content-type and the rendered
    performance.html template (not a fallback / error page).
    """
    resp = client.get("/performance")
    assert resp.status_code == 200, (
        f"GET /performance must return 200, got {resp.status_code}"
    )
    ctype = resp.headers.get("Content-Type", "")
    assert "text/html" in ctype.lower(), (
        f"Content-Type must include 'text/html', got {ctype!r}"
    )
    # Spot-check the rendered body — the template should at minimum contain a
    # title or heading that identifies the page.  Case-insensitive to keep the
    # contract loose around copy choices.
    body = resp.get_data(as_text=True)
    assert "performance" in body.lower(), (
        "rendered body must reference 'performance' (page title / heading)"
    )


def test_get_performance_includes_chartjs_cdn_reference(client, mock_analytics):
    """
    The Performance tab plots live-vs-shadow return curves with Chart.js.
    The rendered template must include a Chart.js CDN script reference (the
    existing index.html uses ``cdn.jsdelivr.net/npm/chart.js``; we don't pin
    the exact URL — only that *some* Chart.js script tag is present).
    """
    resp = client.get("/performance")
    body = resp.get_data(as_text=True).lower()
    # Loose: any <script ... src="...chart.js..." ...> reference is acceptable.
    assert "chart.js" in body, (
        "performance.html must reference Chart.js (CDN script tag) for plots"
    )
    assert "<script" in body, "Chart.js must be loaded via a <script> tag"


def test_get_performance_has_scope_toggle_ui(client, mock_analytics):
    """
    The Performance tab must expose a scope toggle so the operator can switch
    between aggregate (whole-portfolio) and per-symphony views.  Implementation
    detail is up to the implementer (<select>, radio group, buttons, etc.) but
    *some* element keyed to "scope" or referencing both "aggregate" and a
    symphony-style label must appear in the rendered body.
    """
    resp = client.get("/performance")
    body = resp.get_data(as_text=True).lower()
    # The toggle MUST surface both modes.  Accept either explicit "aggregate"
    # + "symphony" labels or a generic scope attribute.
    has_scope_marker = "scope" in body
    has_aggregate = "aggregate" in body
    has_symphony = "symphony" in body
    assert has_scope_marker or (has_aggregate and has_symphony), (
        "performance.html must include a scope toggle (look for 'scope' or "
        "both 'aggregate' and 'symphony' labels)"
    )


# ---------------------------------------------------------------------------
# 2. GET /api/performance?scope=aggregate — happy path
# ---------------------------------------------------------------------------


def test_api_performance_aggregate_happy_path_30_days(client, mock_analytics):
    """
    GET /api/performance?scope=aggregate&days=60 with 30 days of fixture data:
    JSON must contain the documented shape with all 7 metric keys and the
    correct observation_count + insufficient_history flag.

    Fixture-derived assertions only — we do NOT hardcode any of the producer
    values.  The mocked analytics functions return lists of length 30; the test
    asserts the route preserves length and shape.
    """
    n_days = 30
    symphonies = ["sym-A", "sym-B"]
    history = _build_history_fixture(n_days, symphonies)
    mock_analytics.get_history_with_cache_invalidation.return_value = history

    dates = sorted(history.keys())
    live_returns = [0.001 * (i + 1) for i in range(n_days)]
    shadow_returns = [0.002 * (i + 1) for i in range(n_days)]
    mock_analytics.compute_aggregate_returns.return_value = (
        dates,
        live_returns,
        shadow_returns,
    )
    live_metrics = {
        "total_return": 0.05,
        "annualized_return": 0.12,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": -0.03,
        "calmar": 4.0,
        "win_rate": 0.6,
    }
    shadow_metrics = {
        "total_return": 0.07,
        "annualized_return": 0.18,
        "sharpe": 1.4,
        "sortino": 1.8,
        "max_drawdown": -0.025,
        "calmar": 7.2,
        "win_rate": 0.65,
    }
    # The route may call compute_quantstats_metrics twice (live + shadow).
    # Return different values per invocation using side_effect.
    mock_analytics.compute_quantstats_metrics.side_effect = [
        live_metrics,
        shadow_metrics,
    ]

    resp = client.get("/api/performance?scope=aggregate&days=60")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["scope"] == "aggregate"
    # Lengths derive from fixture, not hardcoded values.
    assert len(body["dates"]) == n_days
    assert len(body["live_returns"]) == n_days
    assert len(body["shadow_returns"]) == n_days
    # Metric dicts: 7 documented keys each.
    expected_metric_keys = {
        "total_return",
        "annualized_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "win_rate",
    }
    assert set(body["live_metrics"].keys()) == expected_metric_keys
    assert set(body["shadow_metrics"].keys()) == expected_metric_keys
    assert body["observation_count"] == n_days
    assert body["insufficient_history"] is False

    # All return values must be floats (shape, not value).
    assert all(isinstance(r, float) for r in body["live_returns"])
    assert all(isinstance(r, float) for r in body["shadow_returns"])


def test_api_performance_aggregate_insufficient_history_flag(client, mock_analytics):
    """
    A 15-day window must surface ``insufficient_history=True`` so the dashboard
    can render the "not enough history yet" UI state.  The exact threshold lives
    in analytics.py / the implementer's discretion; this test pins the contract
    that 15 days < 30-day-floor produces the flag.
    """
    n_days = 15
    history = _build_history_fixture(n_days, ["sym-A"])
    dates = sorted(history.keys())
    live_returns = [0.001 * (i + 1) for i in range(n_days)]
    shadow_returns = [0.002 * (i + 1) for i in range(n_days)]

    mock_analytics.get_history_with_cache_invalidation.return_value = history
    mock_analytics.compute_aggregate_returns.return_value = (
        dates,
        live_returns,
        shadow_returns,
    )
    mock_analytics.compute_quantstats_metrics.return_value = {
        "total_return": None,
        "annualized_return": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "calmar": None,
        "win_rate": None,
    }

    resp = client.get("/api/performance?scope=aggregate&days=60")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["observation_count"] == n_days
    assert body["insufficient_history"] is True


def test_api_performance_aggregate_empty_history(client, mock_analytics):
    """
    Empty history dict from analytics yields observation_count=0,
    insufficient_history=True, and every metric value None.
    """
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.compute_aggregate_returns.return_value = ([], [], [])
    mock_analytics.compute_quantstats_metrics.return_value = {
        "total_return": None,
        "annualized_return": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "calmar": None,
        "win_rate": None,
    }

    resp = client.get("/api/performance?scope=aggregate&days=60")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["observation_count"] == 0
    assert body["insufficient_history"] is True
    # Every metric value must be None when no observations exist.
    for v in body["live_metrics"].values():
        assert v is None
    for v in body["shadow_metrics"].values():
        assert v is None


# ---------------------------------------------------------------------------
# 3. GET /api/performance?scope=symphony — per-symphony
# ---------------------------------------------------------------------------


def test_api_performance_symphony_happy_path(client, mock_analytics):
    """
    scope=symphony with a valid symphony_id returns that symphony's series only.

    We seed the history with two symphonies and configure
    ``compute_per_symphony_returns`` to return only the requested one's data;
    then we assert the route actually called the per-symphony helper with the
    correct symphony_id and that the response carries the right series length.
    """
    n_days = 20
    symphonies = ["sym-A", "sym-B"]
    history = _build_history_fixture(n_days, symphonies)
    mock_analytics.get_history_with_cache_invalidation.return_value = history

    sym_a_dates = sorted(history.keys())
    sym_a_live = [0.001 * (i + 1) for i in range(n_days)]
    sym_a_shadow = [0.002 * (i + 1) for i in range(n_days)]
    mock_analytics.compute_per_symphony_returns.return_value = (
        sym_a_dates,
        sym_a_live,
        sym_a_shadow,
    )
    mock_analytics.compute_quantstats_metrics.return_value = {
        "total_return": 0.03,
        "annualized_return": 0.10,
        "sharpe": 1.0,
        "sortino": 1.1,
        "max_drawdown": -0.02,
        "calmar": 5.0,
        "win_rate": 0.55,
    }

    resp = client.get("/api/performance?scope=symphony&symphony_id=sym-A")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["scope"] == "symphony"
    # Per-symphony helper was invoked with the requested symphony_id.
    assert mock_analytics.compute_per_symphony_returns.called
    call_args = mock_analytics.compute_per_symphony_returns.call_args
    # symphony_id may be passed positionally or via kwarg; accept either.
    positional = call_args.args
    kwargs = call_args.kwargs
    sym_id_used = kwargs.get("symphony_id") if "symphony_id" in kwargs else (
        positional[1] if len(positional) >= 2 else None
    )
    assert sym_id_used == "sym-A", (
        f"compute_per_symphony_returns must be called with sym-A, got {sym_id_used!r}"
    )

    # Returned series length derives from the fixture, not hardcoded.
    assert len(body["dates"]) == n_days
    assert len(body["live_returns"]) == n_days
    assert len(body["shadow_returns"]) == n_days
    assert body["observation_count"] == n_days


def test_api_performance_symphony_without_symphony_id_returns_400(client, mock_analytics):
    """
    scope=symphony with no symphony_id query arg is an operator error.

    Contract choice: 400 Bad Request with an error message — the empty-state
    case is reserved for "valid id, no data" (covered by the next test) so a
    missing id must be a hard 400 to surface the URL/UI bug fast.
    """
    resp = client.get("/api/performance?scope=symphony")
    assert resp.status_code == 400, (
        "scope=symphony without symphony_id must return 400, got "
        f"{resp.status_code}"
    )
    body = resp.get_json()
    # Body MUST carry an error indicator — either a "status": "error" envelope
    # or an "error" key.  Implementer's choice; both are acceptable.
    assert body is not None, "400 response must include a JSON error body"
    has_error_signal = (
        body.get("status") == "error"
        or "error" in body
        or "message" in body
    )
    assert has_error_signal, (
        f"400 response must include an error/message field, got body={body!r}"
    )


def test_api_performance_symphony_unknown_id_returns_empty_state(client, mock_analytics):
    """
    scope=symphony with a syntactically-valid but non-existent symphony_id
    returns 200 with empty data + insufficient_history=True.  This is NOT a
    404 — gracefully empty so the dashboard can render "no data yet" without
    treating it as an error.
    """
    history = _build_history_fixture(10, ["sym-A"])
    mock_analytics.get_history_with_cache_invalidation.return_value = history
    # Unknown id => empty series
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    mock_analytics.compute_quantstats_metrics.return_value = {
        "total_return": None,
        "annualized_return": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "calmar": None,
        "win_rate": None,
    }

    resp = client.get("/api/performance?scope=symphony&symphony_id=does-not-exist")
    assert resp.status_code == 200, (
        "unknown symphony_id must be a graceful empty-state, not 404"
    )
    body = resp.get_json()
    assert body["observation_count"] == 0
    assert body["insufficient_history"] is True
    assert body["dates"] == []
    assert body["live_returns"] == []
    assert body["shadow_returns"] == []


# ---------------------------------------------------------------------------
# 4. GET /api/performance/symphonies — dropdown list
# ---------------------------------------------------------------------------


def test_api_performance_symphonies_returns_sorted_list(client, mock_analytics):
    """
    GET /api/performance/symphonies returns ``{"symphonies": [...]}`` for the
    UI dropdown.  We mock ``list_available_symphonies`` and assert the values
    flow through unchanged.
    """
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = ["sym-A", "sym-B"]

    resp = client.get("/api/performance/symphonies")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "symphonies" in body
    assert body["symphonies"] == ["sym-A", "sym-B"]


# ---------------------------------------------------------------------------
# 5. Read-only contract — no mutation surfaces touched
# ---------------------------------------------------------------------------


def test_performance_routes_do_not_call_database_save_or_requests_post(
    client, mock_analytics
):
    """
    Performance routes MUST be read-only — they must never invoke any
    ``database.save_*`` function, never call ``requests.post``.  This pins the
    dashboard-as-read-only-surface constraint from project CLAUDE.md.

    We patch the entire ``database`` module *and* requests.post; if any
    Performance route reaches for them, the assertion below catches it.
    """
    # Seed analytics so the routes have something to render.
    history = _build_history_fixture(5, ["sym-A"])
    mock_analytics.get_history_with_cache_invalidation.return_value = history
    mock_analytics.compute_aggregate_returns.return_value = (
        sorted(history.keys()),
        [0.001 * (i + 1) for i in range(5)],
        [0.002 * (i + 1) for i in range(5)],
    )
    mock_analytics.list_available_symphonies.return_value = ["sym-A"]

    with patch.object(app_module, "database") as db_mock, patch.object(
        app_module.requests, "post"
    ) as requests_post_mock:
        # Hit every Performance route.
        client.get("/performance")
        client.get("/api/performance?scope=aggregate&days=60")
        client.get("/api/performance?scope=symphony&symphony_id=sym-A")
        client.get("/api/performance/symphonies")

        # Verify no save_* on database was called.
        for attr_name in dir(db_mock):
            if attr_name.startswith("save_"):
                save_attr = getattr(db_mock, attr_name)
                assert not save_attr.called, (
                    f"Performance routes must not call database.{attr_name}() "
                    f"— dashboard is read-only"
                )

        # requests.post must never fire from a Performance route.
        assert not requests_post_mock.called, (
            "Performance routes must not POST to any external API"
        )


def test_performance_routes_do_not_acquire_database_lock(client, mock_analytics):
    """
    Performance routes MUST NOT call ``database.acquire_lock()``.  The live
    engine holds that lock while it cycles every minute at :00; blocking the
    dashboard on it would couple operator UI latency to engine cycle time.
    Pins the "no blocking I/O on the execution path" architecture constraint.
    """
    history = _build_history_fixture(5, ["sym-A"])
    mock_analytics.get_history_with_cache_invalidation.return_value = history
    mock_analytics.compute_aggregate_returns.return_value = (
        sorted(history.keys()),
        [0.001 * (i + 1) for i in range(5)],
        [0.002 * (i + 1) for i in range(5)],
    )
    mock_analytics.list_available_symphonies.return_value = ["sym-A"]

    with patch.object(app_module, "database") as db_mock:
        client.get("/performance")
        client.get("/api/performance?scope=aggregate&days=60")
        client.get("/api/performance?scope=symphony&symphony_id=sym-A")
        client.get("/api/performance/symphonies")

        assert not db_mock.acquire_lock.called, (
            "Performance routes must not call database.acquire_lock() — "
            "the live engine holds that lock at the top of every minute"
        )


# ---------------------------------------------------------------------------
# 6. Input validation — invalid scope
# ---------------------------------------------------------------------------


def test_api_performance_invalid_scope_returns_400(client, mock_analytics):
    """
    An unknown ``scope`` value (e.g. ``scope=foo``) must be rejected as a 400
    so that a typo in the UI does not silently fall through to a default mode.
    """
    resp = client.get("/api/performance?scope=foo")
    assert resp.status_code == 400, (
        f"scope=foo must return 400, got {resp.status_code}"
    )
    body = resp.get_json()
    assert body is not None
    has_error_signal = (
        body.get("status") == "error"
        or "error" in body
        or "message" in body
    )
    assert has_error_signal, (
        f"invalid-scope 400 must include an error/message field, got {body!r}"
    )
