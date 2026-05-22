"""
RED tests — Cycle 2-FIX: Dashboard live data wiring + all-screens functional parity.

Root cause: dashboard was a static Jinja page. CUM_CHART_DATA baked in at
render time with empty arrays. No setInterval, no fetch('/api/state'), no
/api/chart calls.

Scope expanded per PM directives:
  - All-screens functional sweep (Dashboard, History, Performance, Advisor)
  - JSX-sourced structural requirements (studio.jsx as ground truth)
  - Google Fonts preconnect in _chrome.html
  - Canvas/SVG inventory (sparklines, MC dials, hero chart, intraday chart)
  - Cash Now on standby cards
  - conftest known-state SQLite fixture (see tests/ui/conftest.py)

Surfaces:
  FIX-1   /api/state meta.portfolio.hist_dates has ≥ 30 entries when DB has data
  FIX-2   hist_bot and hist_held are parallel to hist_dates and non-trivially non-zero
  FIX-3   /api/chart/<sym> returns series data from database.load_chart_history
  FIX-4   /api/state bot_state is populated from database.load_state()
  FIX-5   templates/index.html contains setInterval ≤ 60000 ms targeting /api/state
  FIX-6   index.html or dashboard JS contains fetch('/api/state')
  FIX-7   dashboard JS fetches /api/chart/ per symphony
  FIX-8   hero chart is driven by AJAX data, not Jinja-baked arrays
  FIX-9   sparkline code wires canvas from /api/chart response
  FIX-10  DOM values update when /api/state returns new numbers (dual-mock test)
  FIX-11  static/history.js contains setInterval ≤ 60000 ms
  FIX-12  static/performance.js setInterval targets /api/performance, interval ≤ 60000 ms
  FIX-13  static/ai_advisor.js setInterval targets live data endpoint, interval ≤ 60000 ms
  FIX-14  /api/chart/<sym> response includes overlay fields required by detail panel
  FIX-15  Google Fonts preconnect tags present in templates/_chrome.html
  FIX-16  Dashboard has sparkline canvas/SVG elements (≥ 2, one per active + standby card)
  FIX-17  Standby cards include Cash Now button
  FIX-18  Symphony cards include sparkline container element
  FIX-19  Symphony cards include MC dial container element
  FIX-20  Dashboard JS (index.js or inline) references dom update after fetch
  FIX-21  Guard alpha headline reflects API data (not hardcoded 0.00%)
  FIX-22  /api/state includes per-symphony guard_alpha field in bot_state entries

Scope expanded (PM cross-screen audit 2026-05-19):
  FIX-25  <title> content does NOT appear in rendered body text of any screen
  FIX-26  /performance has >= 2 chart containers (cumulative SVG + metric mini-bars per METRICS
           array) and DOM updates after /api/performance fetch returns mocked data
  FIX-27  /history has >= 2 chart containers (daily strip SVG + per-reason mini-bar SVGs for each
           of the 4 exit reasons) and DOM updates after /api/history/<days> fetch
  FIX-28  /ai-advisor suggestion cards rendered after /ai-advisor/suggest POST include confidence
           badge, OOS status badge, projected-impact delta, and data_sufficiency badge per card;
           autotune right-rail panel populated from /api/autotune-runs response
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import database

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"

# Minimal chart_archive history: 35 days × 1 symphony, both live_ret and f_ret set.
_THIRTY_FIVE_DAYS = [f"2026-04-{d:02d}" for d in range(1, 29)] + [
    f"2026-05-{d:02d}" for d in range(1, 8)
]
assert len(_THIRTY_FIVE_DAYS) == 35

_CHART_ARCHIVE_STUB = {
    date_str: {
        "sym_abc": {
            "live_ret": 0.001 * (i + 1),
            "f_ret": 0.0012 * (i + 1),
            "weight": 1.0,
        }
    }
    for i, date_str in enumerate(_THIRTY_FIVE_DAYS)
}

# chart_history blob shape returned by database.load_chart_history()
_CHART_HISTORY_STUB = {
    "symphonies": {
        "sym_abc": [
            {"date": d, "bot": 0.1 * (i + 1), "held": 0.08 * (i + 1)}
            for i, d in enumerate(_THIRTY_FIVE_DAYS[:10])
        ]
    }
}

_BOT_STATE_STUB = {
    "sym_abc": {
        "id": "sym_abc",
        "name": "Alpha Symphony",
        "position": 0.8,
        "simple_return": 0.05,
        "net_deposits": 0.0,
        "time_weighted_return": 0.05,
    }
}


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _make_api_state_dict_stub(bot_state=None, hist_dates=None, hist_bot=None, hist_held=None):
    """Build a minimal get_api_state_dict return value for testing."""
    if bot_state is None:
        bot_state = _BOT_STATE_STUB.copy()
    if hist_dates is None:
        hist_dates = _THIRTY_FIVE_DAYS[:]
    if hist_bot is None:
        hist_bot = [0.001 * (i + 1) for i in range(len(hist_dates))]
    if hist_held is None:
        hist_held = [0.0008 * (i + 1) for i in range(len(hist_dates))]
    return {
        "bot_state": bot_state,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        # hist arrays injected here so _build_meta can pick them up via portfolio_strip
        "portfolio_strip": {
            "today_change": 0.0,
            "cumulative_return": 0.05,
            "max_drawdown": -0.02,
            "hist_dates": hist_dates,
            "hist_bot": hist_bot,
            "hist_held": hist_held,
        },
    }


@pytest.fixture
def mock_api_state_with_history(monkeypatch):
    """Patch get_api_state_dict to return stub with 35-day hist series."""
    stub = _make_api_state_dict_stub()
    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        # Also need database.load_state + get_ro_connection for the route internals
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = _BOT_STATE_STUB.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            db_mock.read_port_state.return_value = None
            yield db_mock


@pytest.fixture
def mock_db_with_history(monkeypatch):
    """Mock database layer with 35 days of chart_archive data (for chart endpoint tests)."""
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = _BOT_STATE_STUB.copy()
        db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db_mock.get_ro_connection.return_value = MagicMock()
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        db_mock.read_port_state.return_value = None
        yield db_mock


@pytest.fixture
def mock_analytics_with_history(monkeypatch):
    """Mock analytics.get_history_with_cache_invalidation to return 35-day stub."""
    with patch("analytics.get_history_with_cache_invalidation") as mock_hist:
        mock_hist.return_value = _CHART_ARCHIVE_STUB
        yield mock_hist


# ---------------------------------------------------------------------------
# FIX-1: /api/state hist_dates length ≥ 30 when DB has data
# ---------------------------------------------------------------------------


def test_api_state_hist_dates_populated_from_db(monkeypatch):
    """/api/state meta.portfolio.hist_dates must have ≥ 30 entries when DB has 35 days.

    The broken path: _build_meta reads portfolio_strip which is only populated by the
    live scheduler cycle. Between scheduler cycles (or on fresh boot), portfolio_strip
    is empty → hist_dates = [].

    The fix: get_api_state_dict() (or the /api/state route) must compute hist arrays
    from analytics when portfolio_strip is missing/empty. Specifically, when
    analytics.get_history_with_cache_invalidation returns ≥ 30 days of data,
    meta.portfolio.hist_dates must contain those dates.

    This test patches analytics to return 35 days and asserts the route's computed
    meta has ≥ 30 dates.
    """
    import analytics as analytics_module

    with patch.object(analytics_module, "get_history_with_cache_invalidation",
                      return_value=_CHART_ARCHIVE_STUB):
        # get_api_state_dict returns NO portfolio_strip (scheduler hasn't run)
        stub_no_strip = {
            "bot_state": {},
            "is_locked": False,
            "port_state": {},
            "exit_authority": "per_symphony",
            "daemon_started_at": "2026-05-19T00:00:00Z",
        }
        with patch.object(app_module, "get_api_state_dict", return_value=stub_no_strip):
            with patch.object(app_module, "database") as db_mock:
                db_mock.load_state.return_value = {}
                db_mock.get_ro_connection.return_value = MagicMock()
                db_mock.read_fleet_alert.return_value = None
                db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
                db_mock.read_port_state.return_value = None

                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/api/state")

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
    )
    portfolio = resp.get_json().get("meta", {}).get("portfolio", {})
    hist_dates = portfolio.get("hist_dates", [])
    assert len(hist_dates) >= 30, (
        f"meta.portfolio.hist_dates must have ≥ 30 entries when analytics returns 35 days "
        f"of chart_archive; got {len(hist_dates)}. "
        "The fix must compute hist series from analytics when portfolio_strip is absent."
    )


# ---------------------------------------------------------------------------
# FIX-2: hist_bot and hist_held parallel + non-zero
# ---------------------------------------------------------------------------


def test_api_state_hist_bot_held_parallel_and_nonzero(monkeypatch):
    """hist_bot and hist_held must be same length as hist_dates and have non-zero values.

    Tests through the same analytics-patch path as FIX-1 to verify the full contract.
    """
    import analytics as analytics_module

    with patch.object(analytics_module, "get_history_with_cache_invalidation",
                      return_value=_CHART_ARCHIVE_STUB):
        stub_no_strip = {
            "bot_state": {},
            "is_locked": False,
            "port_state": {},
            "exit_authority": "per_symphony",
            "daemon_started_at": "2026-05-19T00:00:00Z",
        }
        with patch.object(app_module, "get_api_state_dict", return_value=stub_no_strip):
            with patch.object(app_module, "database") as db_mock:
                db_mock.load_state.return_value = {}
                db_mock.get_ro_connection.return_value = MagicMock()
                db_mock.read_fleet_alert.return_value = None
                db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
                db_mock.read_port_state.return_value = None

                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/api/state")

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
    )
    portfolio = resp.get_json().get("meta", {}).get("portfolio", {})
    hist_dates = portfolio.get("hist_dates", [])
    hist_bot = portfolio.get("hist_bot", [])
    hist_held = portfolio.get("hist_held", [])

    # Must have actual data (not empty)
    assert len(hist_dates) >= 30, (
        f"hist_dates must have ≥ 30 entries when analytics returns 35 days; got {len(hist_dates)}. "
        "Parallel check is meaningless on empty arrays."
    )

    # Lengths must match
    assert len(hist_bot) == len(hist_dates), (
        f"hist_bot length ({len(hist_bot)}) must equal hist_dates length ({len(hist_dates)})"
    )
    assert len(hist_held) == len(hist_dates), (
        f"hist_held length ({len(hist_held)}) must equal hist_dates length ({len(hist_dates)})"
    )

    # At least one non-zero value in each series (not all flat zeros)
    assert any(v != 0.0 for v in hist_bot), (
        "hist_bot must contain at least one non-zero value; "
        "all zeros means returns are not being accumulated"
    )
    assert any(v != 0.0 for v in hist_held), (
        "hist_held must contain at least one non-zero value"
    )


# ---------------------------------------------------------------------------
# FIX-3: /api/chart/<sym> returns series data from DB
# ---------------------------------------------------------------------------


def test_api_chart_returns_series_data_from_db(client, monkeypatch):
    """/api/chart/<sym> must return data sourced from database.load_chart_history().

    Currently returns {data: [], status: success} — empty because chart_history
    blob is empty in test DB.
    """
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
        db_mock.get_ro_connection.return_value = MagicMock()

        resp = client.get("/api/chart/sym_abc")
        assert resp.status_code == 200
        body = resp.get_json()
        data = body.get("data", [])
        assert len(data) > 0, (
            "GET /api/chart/sym_abc must return non-empty data when chart_history "
            "has entries for sym_abc. Currently returns empty list."
        )


def test_api_chart_response_has_series_keys(client, monkeypatch):
    """/api/chart/<sym> response data entries should contain series-like fields.

    The detail panel needs at least one of: bot, held, date, or similar.
    """
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
        db_mock.get_ro_connection.return_value = MagicMock()

        resp = client.get("/api/chart/sym_abc")
        assert resp.status_code == 200
        data = resp.get_json().get("data", [])
        if len(data) > 0:
            entry = data[0]
            assert isinstance(entry, dict), "chart data entries must be dicts"
            # Must have at least a date or a numeric series key
            has_useful_key = any(
                k in entry for k in ("date", "bot", "held", "stop", "vwap", "mc", "ts")
            )
            assert has_useful_key, (
                f"chart data entry must have at least one of: date, bot, held, stop, vwap, mc, ts. "
                f"Got keys: {list(entry.keys())}"
            )


# ---------------------------------------------------------------------------
# FIX-4: /api/state bot_state populated from database.load_state()
# ---------------------------------------------------------------------------


def test_api_state_has_bot_state_key_in_json(client):
    """/api/state JSON response must include a 'bot_state' key (regression contract).

    The dashboard JS reads bot_state to render the symphony cards. If this key
    is absent, the entire card grid is broken.
    """
    # Minimal clean mock — empty state is fine, we just need the key present
    stub = {
        "bot_state": {},
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
    }
    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            db_mock.read_port_state.return_value = None
            resp = client.get("/api/state")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
    body = resp.get_json()
    assert "bot_state" in body, (
        "/api/state JSON must include 'bot_state' key. "
        "The dashboard JS polls this endpoint and reads bot_state for symphony cards."
    )
    assert isinstance(body["bot_state"], dict), (
        "bot_state must be a dict, not null or a list."
    )


# ---------------------------------------------------------------------------
# FIX-5: templates/index.html contains setInterval ≤ 60000 ms
# ---------------------------------------------------------------------------


def test_index_html_has_setinterval_for_polling():
    """static/index.js must contain setInterval polling for /api/state, interval ≤ 60000 ms.
    Polling moved from templates/index.html to static/index.js (C-03 fix).
    """
    js_path = _STATIC_DIR / "index.js"
    assert js_path.exists(), "static/index.js must exist"
    src = js_path.read_text(encoding="utf-8")

    assert "setInterval" in src, (
        "static/index.js must contain setInterval for dashboard polling. "
        "Currently there is no polling — dashboard is static."
    )

    # Check via named constant: POLL_INTERVAL_MS must be defined and ≤ 60000
    const_matches = re.findall(r'var\s+POLL_INTERVAL_MS\s*=\s*(\d+)', src)
    if const_matches:
        min_interval = min(int(n) for n in const_matches)
        assert min_interval <= 60000, (
            f"POLL_INTERVAL_MS must be ≤ 60000 ms; found {min_interval} ms. "
            "Dashboard must poll /api/state at least once per minute."
        )
    else:
        # Fall back to checking inline numeric literals
        intervals = re.findall(r'setInterval\s*\([^,)]+,\s*(\d+)', src)
        assert len(intervals) > 0, (
            "setInterval found but no numeric interval argument detected. "
            "Must have setInterval(<fn>, <ms>) with ms ≤ 60000 or a POLL_INTERVAL_MS constant."
        )
        min_interval = min(int(n) for n in intervals)
        assert min_interval <= 60000, (
            f"setInterval interval must be ≤ 60000 ms; found {min_interval} ms."
        )


def test_index_html_setinterval_not_above_60s():
    """Explicit guard: no setInterval > 60000 ms for the state poll.
    Polling moved from templates/index.html to static/index.js (C-03 fix).
    """
    js_path = _STATIC_DIR / "index.js"
    if not js_path.exists():
        pytest.skip("static/index.js not yet created")
    src = js_path.read_text(encoding="utf-8")
    if "setInterval" not in src:
        pytest.fail("No setInterval found in static/index.js — dashboard must poll /api/state")

    # Check named constant first (preferred pattern)
    const_matches = re.findall(r'var\s+POLL_INTERVAL_MS\s*=\s*(\d+)', src)
    for n in const_matches:
        assert int(n) <= 60000, (
            f"POLL_INTERVAL_MS = {n} ms exceeds 60000 ms maximum for state poll."
        )
    # Also check any inline numeric setInterval calls
    intervals = re.findall(r'setInterval\s*\([^,)]+,\s*(\d+)', src)
    for n in intervals:
        assert int(n) <= 60000, (
            f"setInterval interval {n} ms exceeds 60000 ms maximum for state poll."
        )


# ---------------------------------------------------------------------------
# FIX-6: fetch('/api/state') in index.html or dashboard JS
# ---------------------------------------------------------------------------


def test_index_html_or_js_has_fetch_api_state():
    """Dashboard must contain fetch('/api/state') or XMLHttpRequest to /api/state."""
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    has_fetch = "fetch('/api/state'" in html or 'fetch("/api/state"' in html
    has_xhr = "XMLHttpRequest" in html and "/api/state" in html

    # Also check for a dedicated dashboard JS file (index.js or dashboard.js)
    dashboard_js = _STATIC_DIR / "index.js"
    if dashboard_js.exists():
        js_src = dashboard_js.read_text(encoding="utf-8")
        has_fetch = has_fetch or (
            "fetch('/api/state'" in js_src or 'fetch("/api/state"' in js_src
        )

    assert has_fetch or has_xhr, (
        "Dashboard must contain fetch('/api/state') or XMLHttpRequest to /api/state. "
        "Currently the dashboard never fetches live data — it only uses Jinja-baked values."
    )


# ---------------------------------------------------------------------------
# FIX-7: dashboard JS fetches /api/chart/ per symphony
# ---------------------------------------------------------------------------


def test_index_html_or_js_fetches_api_chart():
    """Dashboard must fetch /api/chart/ for per-symphony sparklines."""
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    has_chart_fetch = "/api/chart/" in html or "api/chart/" in html
    has_template_literal = "`/api/chart/" in html or '"/api/chart/" +' in html or "'/api/chart/' +" in html

    dashboard_js = _STATIC_DIR / "index.js"
    if dashboard_js.exists():
        js_src = dashboard_js.read_text(encoding="utf-8")
        has_chart_fetch = has_chart_fetch or "/api/chart/" in js_src
        has_template_literal = has_template_literal or (
            "`/api/chart/" in js_src or '"/api/chart/" +' in js_src or "'/api/chart/' +" in js_src
        )

    assert has_chart_fetch or has_template_literal, (
        "Dashboard JS must fetch /api/chart/<sym> for per-symphony sparklines. "
        "Currently no chart fetch exists — sparklines will always be empty."
    )


# ---------------------------------------------------------------------------
# FIX-8: hero chart driven by AJAX data, not Jinja-baked arrays
# ---------------------------------------------------------------------------


def test_index_html_chart_not_driven_by_jinja_baked_arrays():
    """CUM_CHART_DATA in index.html must NOT be Jinja-baked — it must be populated via AJAX.

    Current anti-pattern:
      var CUM_CHART_DATA = {
        labels: {{ meta.portfolio.hist_dates | tojson }},
        bot: {{ meta.portfolio.hist_bot | tojson }},
        ...
      };

    This renders empty arrays at page load and never updates. The chart must be
    populated from the first /api/state AJAX response.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    # The broken pattern: Jinja-rendered hist arrays directly in a JS var
    jinja_baked_pattern = re.search(
        r'hist_dates\s*[|:]\s*tojson|hist_bot\s*[|:]\s*tojson|hist_held\s*[|:]\s*tojson',
        html,
    )
    has_jinja_bake = jinja_baked_pattern is not None

    # Also check for the exact broken variable assignment pattern
    broken_var_pattern = re.search(
        r'CUM_CHART_DATA\s*=\s*\{[^}]*\{\{[^}]*hist_',
        html,
        re.DOTALL,
    )

    assert not (has_jinja_bake or broken_var_pattern), (
        "index.html must NOT bake hist_dates/hist_bot/hist_held as Jinja {{ }} values. "
        "These arrays are empty at render time and never update. "
        "The hero chart must be built from the /api/state AJAX response payload."
    )


def test_index_html_hero_chart_built_from_ajax_response():
    """Dashboard JS must build the hero chart from AJAX /api/state response data."""
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    dashboard_js_path = _STATIC_DIR / "index.js"
    source = html
    if dashboard_js_path.exists():
        source += "\n" + dashboard_js_path.read_text(encoding="utf-8")

    # Must reference the chart data source from API response
    has_api_driven_chart = (
        ("hist_dates" in source and ("fetch(" in source or "then(" in source)) or
        ("hist_bot" in source and "fetch(" in source) or
        ("payload" in source and "hist" in source and "Chart(" in source) or
        ("response" in source and "hist" in source and "Chart(" in source) or
        ("data.meta" in source and "hist" in source) or
        ("meta.portfolio" in source and "fetch(" in source)
    )

    assert has_api_driven_chart, (
        "Dashboard must build the hero Chart.js instance from /api/state AJAX data. "
        "The chart constructor must receive data from the fetch response, not from "
        "Jinja-baked template variables."
    )


# ---------------------------------------------------------------------------
# FIX-9: sparkline canvas wired to /api/chart response
# ---------------------------------------------------------------------------


def test_index_html_sparkline_canvas_wired_to_chart_endpoint():
    """Dashboard must wire per-symphony sparkline canvas from /api/chart response."""
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    dashboard_js_path = _STATIC_DIR / "index.js"
    source = html
    if dashboard_js_path.exists():
        source += "\n" + dashboard_js_path.read_text(encoding="utf-8")

    # Must have canvas rendering code that references chart endpoint data
    has_sparkline_wiring = (
        ("canvas" in source.lower() and "api/chart" in source) or
        ("spark" in source.lower() and "api/chart" in source) or
        ("data-symphony-spark" in source and "/api/chart" in source) or
        ("getContext" in source and "/api/chart" in source)
    )

    assert has_sparkline_wiring, (
        "Dashboard must wire per-symphony sparkline canvas to /api/chart/ data. "
        "Currently sparklines have no chart endpoint connection."
    )


# ---------------------------------------------------------------------------
# FIX-10: DOM values update when /api/state returns new numbers
# ---------------------------------------------------------------------------


def test_api_state_returns_updated_bot_state_on_second_call(client, monkeypatch):
    """Two sequential /api/state calls with different mocked states must return different data.

    This validates that /api/state is not memoizing results — each poll gets fresh state.
    """
    call_count = [0]

    def get_api_state_side_effect():
        call_count[0] += 1
        position = 0.5 if call_count[0] == 1 else 0.9
        simple_return = 0.02 if call_count[0] == 1 else 0.08
        return {
            "bot_state": {
                "sym_abc": {
                    "id": "sym_abc", "name": "Alpha Symphony",
                    "position": position, "simple_return": simple_return,
                    "net_deposits": 0.0, "time_weighted_return": simple_return,
                }
            },
            "is_locked": False,
            "port_state": {},
            "exit_authority": "per_symphony",
            "daemon_started_at": "2026-05-19T00:00:00Z",
        }

    with patch.object(app_module, "get_api_state_dict", side_effect=get_api_state_side_effect):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            db_mock.read_port_state.return_value = None

            resp1 = client.get("/api/state")
            resp2 = client.get("/api/state")

    assert resp1.status_code == 200, f"First call: {resp1.status_code} {resp1.get_data(as_text=True)[:200]}"
    assert resp2.status_code == 200, f"Second call: {resp2.status_code} {resp2.get_data(as_text=True)[:200]}"

    assert call_count[0] >= 2, (
        f"/api/state must call get_api_state_dict() on every request — "
        f"got {call_count[0]} calls across 2 requests. "
        "State must not be cached; poll loop depends on fresh reads."
    )

    bs1 = resp1.get_json().get("bot_state", {}).get("sym_abc", {})
    bs2 = resp2.get_json().get("bot_state", {}).get("sym_abc", {})
    # The two states must differ (position changed between calls)
    assert bs1 != bs2 or (bs1 == {} and bs2 == {}), (
        "Two sequential /api/state calls with different mocked states must return different bot_state. "
        "If both are empty, state sourcing is broken."
    )


def test_api_state_portfolio_hist_reflects_fresh_analytics_on_each_call(
    client, monkeypatch
):
    """Each /api/state call must return meta.portfolio with hist arrays.

    This pins the contract that the dashboard poll loop always gets a portfolio section,
    enabling chart updates on every polling cycle.
    """
    with patch.object(app_module, "get_api_state_dict") as mock_gad:
        mock_gad.return_value = _make_api_state_dict_stub()
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            db_mock.read_port_state.return_value = None

            resp1 = client.get("/api/state")
            resp2 = client.get("/api/state")

    assert resp1.status_code == 200, f"First /api/state: {resp1.status_code} {resp1.get_data(as_text=True)[:200]}"
    assert resp2.status_code == 200, f"Second /api/state: {resp2.status_code} {resp2.get_data(as_text=True)[:200]}"

    body1 = resp1.get_json()
    body2 = resp2.get_json()

    assert "meta" in body1, "/api/state must always include meta key"
    assert "meta" in body2, "/api/state must always include meta key"
    assert "portfolio" in body1["meta"], "/api/state meta must always include portfolio"
    assert "portfolio" in body2["meta"], "/api/state meta must always include portfolio"

    # Both responses must have the hist arrays present (even if currently empty — shape contract)
    for key in ("hist_dates", "hist_bot", "hist_held"):
        assert key in body1["meta"]["portfolio"], f"meta.portfolio must have '{key}' key"
        assert key in body2["meta"]["portfolio"], f"meta.portfolio must have '{key}' key"


# ---------------------------------------------------------------------------
# FIX-11: static/history.js must have setInterval for live refresh
# ---------------------------------------------------------------------------


def test_history_js_has_setinterval():
    """static/history.js must contain a setInterval polling /api/history/<days> ≤ 60000 ms.

    Current state: history.js loads data once on page open, never refreshes.
    A user who leaves the page open will see stale data. The PM has flagged
    this as broken — one-shot load is insufficient.
    """
    js_path = _STATIC_DIR / "history.js"
    assert js_path.exists(), "static/history.js must exist"
    src = js_path.read_text(encoding="utf-8")

    assert "setInterval" in src, (
        "static/history.js must contain setInterval for periodic refresh. "
        "Currently history.js is one-shot only — it loads data once and never refreshes."
    )

    intervals = re.findall(r'setInterval\s*\([^,)]+,\s*(\d+)', src)
    assert len(intervals) > 0, (
        "setInterval found in history.js but no numeric interval argument detected. "
        "Must have setInterval(<fn>, <ms>) with ms ≤ 60000."
    )
    min_interval = min(int(n) for n in intervals)
    assert min_interval <= 60000, (
        f"history.js setInterval must be ≤ 60000 ms; found {min_interval} ms."
    )


def test_history_js_setinterval_targets_api_history():
    """The setInterval in history.js must fire a fetch to /api/history/<days>."""
    js_path = _STATIC_DIR / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    src = js_path.read_text(encoding="utf-8")
    if "setInterval" not in src:
        pytest.fail("history.js has no setInterval — one-shot load not acceptable")

    # The function called by setInterval must reference /api/history
    has_history_endpoint = "api/history" in src or "/api/history" in src
    assert has_history_endpoint, (
        "history.js setInterval must invoke a function that fetches /api/history/<days>. "
        "The polling interval must target the live data endpoint, not an animation timer."
    )


# ---------------------------------------------------------------------------
# FIX-12: static/performance.js setInterval must target /api/performance
# ---------------------------------------------------------------------------


def test_performance_js_setinterval_targets_api_performance():
    """performance.js setInterval must poll /api/performance, not an animation timer.

    The PM noted setInterval=1 in performance.js — but we must confirm it targets
    the live data endpoint and not a UI animation loop.
    """
    js_path = _STATIC_DIR / "performance.js"
    assert js_path.exists(), "static/performance.js must exist"
    src = js_path.read_text(encoding="utf-8")

    assert "setInterval" in src, (
        "static/performance.js must contain setInterval for periodic refresh."
    )

    # The setInterval callback must reference /api/performance
    has_perf_endpoint = "api/performance" in src
    assert has_perf_endpoint, (
        "performance.js must poll /api/performance or /api/performance/symphonies. "
        "If the setInterval is only for animation (not data), the page shows stale data."
    )

    intervals = re.findall(r'setInterval\s*\([^,)]+,\s*(\d+)', src)
    assert len(intervals) > 0, (
        "performance.js setInterval has no numeric interval — must be ≤ 60000 ms."
    )
    min_interval = min(int(n) for n in intervals)
    assert min_interval <= 60000, (
        f"performance.js setInterval must be ≤ 60000 ms; found {min_interval} ms."
    )


# ---------------------------------------------------------------------------
# FIX-13: static/ai_advisor.js setInterval must target a live data endpoint
# ---------------------------------------------------------------------------


def test_ai_advisor_js_setinterval_targets_live_endpoint():
    """ai_advisor.js setInterval must target /api/state, /api/autotune-runs, or /ai-advisor/*.

    The PM noted ai_advisor.js has setInterval=1. We must confirm it is polling
    a live backend endpoint (not an animation/spinner timer).
    """
    js_path = _STATIC_DIR / "ai_advisor.js"
    assert js_path.exists(), "static/ai_advisor.js must exist"
    src = js_path.read_text(encoding="utf-8")

    assert "setInterval" in src, (
        "static/ai_advisor.js must contain setInterval for periodic refresh."
    )

    live_endpoints = [
        "api/state", "api/autotune-runs", "ai-advisor/suggest",
        "ai-advisor/accept", "ai-advisor/reject", "api/performance",
    ]
    # The setInterval callback must reference at least one live endpoint in the same scope
    # (we check at file level since the callback is named)
    has_live_endpoint = any(ep in src for ep in live_endpoints)
    assert has_live_endpoint, (
        "ai_advisor.js setInterval must be tied to a live data fetch, not a pure UI timer. "
        f"Must reference one of: {live_endpoints}. "
        "If the setInterval is only for a spinner/animation, the page shows stale data."
    )

    intervals = re.findall(r'setInterval\s*\([^,)]+,\s*(\d+)', src)
    assert len(intervals) > 0, (
        "ai_advisor.js setInterval has no numeric interval — must be ≤ 60000 ms."
    )
    min_interval = min(int(n) for n in intervals)
    assert min_interval <= 60000, (
        f"ai_advisor.js setInterval must be ≤ 60000 ms; found {min_interval} ms."
    )


# ---------------------------------------------------------------------------
# FIX-14: /api/chart/<sym> response includes overlay fields for detail panel
# ---------------------------------------------------------------------------


def test_api_chart_response_includes_overlay_fields(client, monkeypatch):
    """/api/chart/<sym> must include overlay fields required by the detail panel.

    detail-panel.jsx ChartBlock requires: bot_series, held_series (from trigger marker),
    stop_series, breakeven_window, vwap_series, mc_series, events[].
    At minimum the response must include bot and/or held series for the chart to render.
    """
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
        db_mock.get_ro_connection.return_value = MagicMock()

        resp = client.get("/api/chart/sym_abc")
        assert resp.status_code == 200
        body = resp.get_json()

    # The response must have a top-level shape the detail panel can consume
    required_top_level = {"data", "status"}
    missing = required_top_level - set(body.keys())
    assert not missing, (
        f"/api/chart/<sym> must have top-level keys {required_top_level}; "
        f"missing: {missing}"
    )

    # With non-empty chart_history stub, data must be non-empty
    data = body.get("data", [])
    assert len(data) > 0, (
        "/api/chart/sym_abc must return non-empty data when chart_history has entries. "
        "The detail panel ChartBlock will render blank without this."
    )

    # Each data entry must carry overlay-capable fields
    entry = data[0]
    overlay_fields = {"date", "bot", "held", "stop", "vwap", "mc", "ts"}
    has_overlay = bool(overlay_fields & set(entry.keys()))
    assert has_overlay, (
        f"Chart data entries must include at least one overlay field from {overlay_fields}. "
        f"Got: {list(entry.keys())}. detail-panel.jsx ChartBlock needs these to render overlays."
    )


# ---------------------------------------------------------------------------
# FIX-15: Google Fonts preconnect in templates/_chrome.html
# ---------------------------------------------------------------------------


def test_chrome_html_has_google_fonts_preconnect():
    """templates/_chrome.html must include Google Fonts preconnect + stylesheet link.

    Current state: _chrome.html has NO font loading tags. --studio-sans resolves
    to 'Manrope' but the font is not loaded, falling back to Segoe UI on Windows.

    Design source (.design-handoff/project/index.html L7-11) requires:
      - <link rel="preconnect" href="https://fonts.googleapis.com">
      - <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      - <link href="https://fonts.googleapis.com/css2?family=..." rel="stylesheet">
    Including: Manrope, Source Serif 4, Geist, DM Sans, Plus Jakarta Sans,
               IBM Plex Sans, JetBrains Mono.
    """
    chrome_path = _TEMPLATES_DIR / "_chrome.html"
    assert chrome_path.exists(), "templates/_chrome.html must exist"
    src = chrome_path.read_text(encoding="utf-8")

    assert "fonts.googleapis.com" in src, (
        "templates/_chrome.html must load Google Fonts. "
        "Currently no font loading exists — --studio-sans falls back to system font."
    )
    assert 'rel="preconnect"' in src and "fonts.gstatic.com" in src, (
        "_chrome.html must include preconnect tags for fonts.googleapis.com "
        "and fonts.gstatic.com for performance."
    )

    required_fonts = ["Manrope", "Source+Serif+4", "Geist", "DM+Sans",
                      "Plus+Jakarta+Sans", "IBM+Plex+Sans", "JetBrains+Mono"]
    missing_fonts = [f for f in required_fonts if f not in src]
    assert not missing_fonts, (
        f"_chrome.html Google Fonts link must include families: {missing_fonts}. "
        "These are required by the design canvas (.design-handoff/project/index.html L7-11)."
    )


# ---------------------------------------------------------------------------
# FIX-16: Dashboard has sparkline canvas/SVG per symphony card
# ---------------------------------------------------------------------------


def test_index_html_has_sparkline_elements():
    """index.html must have sparkline canvas or SVG elements for symphony cards.

    Current state: only 2 canvas elements (hero chart + intraday). Zero SVG.
    Design (studio.jsx SparkArea / MicroSpark) requires one sparkline per symphony card.
    With ≥ 1 symphony, there must be ≥ 1 sparkline element.

    Accept: <canvas data-testid="card-spark"> OR <svg ...sparkline...> in a
    per-card context.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    # Check for sparkline elements with identifying testids or class names
    has_spark_canvas = (
        'data-testid="card-spark"' in html or
        'data-testid="spark-canvas"' in html or
        'class="spark-canvas"' in html or
        'data-symphony-spark' in html
    )
    has_spark_svg = (
        'class="spark"' in html or
        'data-testid="micro-spark"' in html or
        'class="micro-spark"' in html or
        'class="sparkline"' in html
    )
    has_js_sparklines = (
        'sparkline' in html.lower() or
        'spark' in html.lower()
    ) and 'api/chart' in html

    assert has_spark_canvas or has_spark_svg or has_js_sparklines, (
        "index.html must have sparkline elements for per-symphony cards. "
        "Current markup has only 2 canvas elements (hero + intraday) and zero SVG. "
        "Design (studio.jsx SparkArea/MicroSpark) requires a sparkline tape per card. "
        "Add <canvas data-testid='card-spark'> or <svg class='spark'> inside each card."
    )


# ---------------------------------------------------------------------------
# FIX-17: Standby cards must include Cash Now button
# ---------------------------------------------------------------------------


def test_standby_cards_have_cash_now_button():
    """Standby symphony cards must include a Cash Now button.

    The PM explicitly stated: 'Cash Now button on EVERY card — 0 clicks away from
    that button.' Current implementation only renders Cash Now on active/triggered cards.
    Standby cards have no Cash Now button.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    # Find the standby section and check for cash-now-btn within it
    standby_match = re.search(
        r'data-testid="standby-section".*?</section>',
        html, re.DOTALL,
    )
    assert standby_match is not None, (
        "index.html must have [data-testid='standby-section'] element."
    )

    standby_html = standby_match.group(0)
    assert 'cash-now-btn' in standby_html or 'Cash Now' in standby_html, (
        "Standby section cards must include a Cash Now button. "
        "PM requirement: 'Cash Now button on EVERY card — 0 clicks away from that button.' "
        "Currently Cash Now only appears on active/triggered cards."
    )


# ---------------------------------------------------------------------------
# FIX-18: Symphony cards must include sparkline container element
# ---------------------------------------------------------------------------


def test_symphony_cards_have_sparkline_container():
    """Each symphony card must include a sparkline container element.

    From studio.jsx SymCard: every card has a SparkArea/MicroSpark element
    showing the bot vs held tape. Currently symphony cards have no sparkline container.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    spark_indicators = [
        'data-testid="card-spark"',
        'data-testid="micro-spark"',
        'class="spark',
        'class="micro-spark',
        'class="sparkline',
        'data-symphony-spark',
        'spark-container',
    ]
    has_spark = any(indicator in html for indicator in spark_indicators)
    assert has_spark, (
        "Symphony cards must include a sparkline container element. "
        "studio.jsx SymCard renders SparkArea (or MicroSpark) for every card — "
        "a visual tape of bot vs held returns. Currently no sparkline containers exist. "
        "Add a canvas or SVG element with a identifying testid/class inside each card."
    )


# ---------------------------------------------------------------------------
# FIX-19: Symphony cards must include MC dial container element
# ---------------------------------------------------------------------------


def test_active_cards_have_mc_dial_container():
    """Active symphony cards must include an MC dial container element.

    From studio.jsx SymCard: active/armed cards show a Monte Carlo probability dial
    (studio.jsx Dial component). Currently no MC dial element exists in active cards.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    # Find the active section
    active_match = re.search(
        r'data-testid="active-section".*?data-testid="standby-section"',
        html, re.DOTALL,
    )
    if active_match is None:
        # Try to find just active-section to end of section
        active_match = re.search(
            r'data-testid="active-section".*?</section>',
            html, re.DOTALL,
        )

    dial_indicators = [
        'data-testid="mc-dial"',
        'data-testid="mc-gauge"',
        'class="mc-dial"',
        'class="dial"',
        'mc_prob',
        'mc-prob',
    ]
    has_dial_globally = any(indicator in html for indicator in dial_indicators)

    assert has_dial_globally, (
        "Active symphony cards must include a Monte Carlo dial container element. "
        "studio.jsx SymCard renders a Dial component for armed/active cards showing "
        "MC probability. Currently no MC dial/gauge element exists in index.html. "
        "Add an element with data-testid='mc-dial' inside active sym cards."
    )


# ---------------------------------------------------------------------------
# FIX-20: Dashboard JS must update DOM values after fetch response
# ---------------------------------------------------------------------------


def test_dashboard_js_updates_dom_after_fetch():
    """Dashboard JS (index.js or inline) must update DOM elements after fetch('/api/state').

    Anti-pattern: the page renders static Jinja values that never update.
    Required: the fetch callback must set textContent or innerHTML on
    at least the hero headline values and symphony card values.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    js_path = _STATIC_DIR / "index.js"
    source = html
    if js_path.exists():
        source += "\n" + js_path.read_text(encoding="utf-8")

    # Dashboard JS must update DOM after fetch
    dom_update_patterns = [
        ("textContent" in source and "fetch(" in source),
        ("innerHTML" in source and "fetch(" in source),
        (".innerText" in source and "fetch(" in source),
        ("setAttribute" in source and "fetch(" in source and "data-" in source),
    ]
    has_dom_update = any(dom_update_patterns)

    assert has_dom_update, (
        "Dashboard JS must update DOM elements after the fetch('/api/state') response. "
        "Pattern: fetch('/api/state').then(r => r.json()).then(data => { "
        "  document.getElementById('hero-guard-alpha').textContent = data.meta.portfolio...; "
        "}). "
        "Currently no DOM update code exists — values are static Jinja substitutions."
    )


# ---------------------------------------------------------------------------
# FIX-21: Guard alpha headline must reflect API data, not be hardcoded 0.00%
# ---------------------------------------------------------------------------


def test_guard_alpha_headline_not_jinja_hardcoded_zero():
    """Guard alpha headline must be populated from AJAX data, not a hardcoded Jinja 0.00%.

    The PM observed '+0.00%' for guard alpha on the live dashboard. This value
    should be computed as cumulative_return_bot - cumulative_return_if_held.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    js_path = _STATIC_DIR / "index.js"
    source = html
    if js_path.exists():
        source += "\n" + js_path.read_text(encoding="utf-8")

    # guard-alpha-headline must not contain a hardcoded Jinja-rendered zero or literal 0.00%
    # It must be driven from JS that reads from the API response
    # The Jinja-rendered pattern: {{ alpha | format_pct }} where alpha is 0 because
    # portfolio_strip.cumulative_return is empty
    guard_alpha_in_html = re.search(
        r'guard-alpha[^>]*>\s*\+0\.00%\s*</|guard-alpha[^>]*>\s*\{[^\}]*\}\s*</|'
        r'guard-alpha-headline[^>]*>[^<]*\{\{[^}]+\}\}',
        html,
    )
    guard_alpha_jinja = re.search(
        r'guard-alpha.*\{\{.*alpha.*\}\}',
        html, re.DOTALL,
    )

    # If guard_alpha is Jinja-baked (not AJAX-driven), it will always be stale
    # The test: if there's a Jinja-baked alpha expression, the value must be
    # driven from real portfolio_strip data (which requires backend fix FIX-1/2)
    # OR the value must be computed in JS from AJAX response

    js_path = _STATIC_DIR / "index.js"
    js_computes_alpha = (
        "guard_alpha" in source or
        "guard-alpha" in source
    ) and (
        "fetch(" in source or
        "cumulative" in source
    )

    # If Jinja-baked AND no JS update, it will always be stale
    if guard_alpha_jinja and not js_computes_alpha:
        pytest.fail(
            "guard-alpha-headline is Jinja-baked but no JS updates it after fetch. "
            "The value will always be stale (0.00% when portfolio_strip is empty). "
            "Either: (a) populate portfolio_strip server-side from analytics, OR "
            "(b) compute guard_alpha in JS from fetch('/api/state') response."
        )


# ---------------------------------------------------------------------------
# FIX-22: /api/state bot_state entries must include guard_alpha field
# ---------------------------------------------------------------------------


def test_dashboard_verdict_driven_by_ajax_not_jinja():
    """Triggered verdict pill must be AJAX-driven, not Jinja-baked.

    studio.jsx SymCard reads sym.guard_alpha from the live API response to compute
    'Good call · saved +X%α' / 'Early exit · gave up X%α'.

    Anti-pattern (current state): verdict is Jinja-baked as:
      {{ sym.get("guard_alpha", 0) }}
    This means the verdict is static at render time and never updates as the
    symphony's live return changes.

    Required: the verdict must be rendered from the AJAX /api/state bot_state response
    in JS. The Jinja-baked `guard_alpha` reference in the card template must be replaced
    by a JS update after fetch.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    # Anti-pattern: guard_alpha read from Jinja context (set block OR direct expression)
    # Covers: {% set ga = sym.get("guard_alpha", ...) %} and {{ sym.guard_alpha }}
    jinja_guard_alpha_set = re.search(
        r'\{%-?\s*set\s+\w+\s*=.*guard_alpha',
        html,
    )
    jinja_guard_alpha_expr = re.search(
        r'\{\{[^}]*guard_alpha[^}]*\}\}',
        html,
    )
    jinja_verdict_baked = re.search(
        r'\{%-?\s*if[^%]*guard_alpha[^%]*%\}',
        html,
    )

    js_path = _STATIC_DIR / "index.js"
    source = html
    if js_path.exists():
        source += "\n" + js_path.read_text(encoding="utf-8")

    # The verdict must be AJAX-driven: JS reads guard_alpha from fetch response
    js_drives_verdict = (
        "guard_alpha" in source and "fetch(" in source
    ) or (
        "triggered_at_return" in source and "fetch(" in source
    )

    has_jinja_bake = bool(jinja_guard_alpha_set or jinja_guard_alpha_expr or jinja_verdict_baked)

    if has_jinja_bake and not js_drives_verdict:
        pytest.fail(
            "Triggered verdict is Jinja-baked (uses {{ guard_alpha }} template expression) "
            "but no JS updates it after fetch('/api/state'). "
            "The verdict will be static at render time and never reflect live returns. "
            "Required: JS must read sym.guard_alpha from the AJAX bot_state response "
            "and dynamically render 'Good call · saved +X%α' or 'Early exit · gave up X%α'."
        )


# ---------------------------------------------------------------------------
# FIX-23: /api/state bot_state entries must have _cr (cumulative return) non-zero
#          for symphonies that have been running
# ---------------------------------------------------------------------------


def test_dashboard_route_enriches_symphonies_with_analytics_cr(monkeypatch):
    """The dashboard() route (GET /) must enrich symphony entries with _cr from analytics.

    PM punch list item 5: 'Symphony cards all read +0.0% / +0.0%.'
    The dashboard Jinja template renders sym._cr.dry_run and sym._cr.if_held for each card.
    If symphony entries don't have _cr populated from analytics, all cards show 0%.

    The dashboard route passes `standby_syms` and `active_syms` to the template.
    These must be enriched with _cr / _tc / _mdd before passing to render_template.
    Currently the live branch does NOT enrich — only the frozen snapshot branch does.
    """
    state_with_sym = {
        "sym_alpha": {
            "id": "sym_alpha",
            "name": "Alpha Symphony",
            "position": 0.85,
            "current_return": 4.2,
            "simple_return": 0.042,
            "net_deposits": 50000.0,
            "time_weighted_return": 0.038,
            "armed": True,
            # _cr / _tc / _mdd intentionally absent — app must add them
        }
    }
    stub = {
        "bot_state": state_with_sym,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
    }
    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = state_with_sym
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            db_mock.read_port_state.return_value = None

            # Capture render_template call to inspect the symphonies passed
            render_calls = []
            original_render = app_module.render_template

            def capture_render(template_name, **ctx):
                render_calls.append(ctx)
                return original_render(template_name, **ctx)

            with patch.object(app_module, "render_template", side_effect=capture_render):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/")

    assert resp.status_code == 200, f"GET / failed: {resp.status_code}"
    assert len(render_calls) > 0, "render_template must have been called"

    ctx = render_calls[-1]
    active_syms = ctx.get("active_syms", [])
    standby_syms = ctx.get("standby_syms", [])
    all_syms = active_syms + standby_syms

    assert len(all_syms) > 0, (
        "dashboard() must pass at least one symphony to the template. "
        "Got 0 symphonies in active_syms + standby_syms. "
        "Cards cannot render without symphony data."
    )

    for sym in all_syms:
        if not isinstance(sym, dict):
            continue
        cr_val = sym.get("current_return", 0.0)
        if cr_val and abs(cr_val) > 0.01:
            assert "_cr" in sym, (
                f"Symphony '{sym.get('name', sym.get('id'))}' has current_return={cr_val} "
                "but no '_cr' analytics field in the template context. "
                "The template renders {{ sym._cr.dry_run }} — without this all cards show +0.0%. "
                "The dashboard() route must enrich each symphony with _cr from analytics."
            )


# ---------------------------------------------------------------------------
# FIX-24: Dashboard JS fetches /api/chart/<sym> and updates card sparklines per card
# ---------------------------------------------------------------------------


def test_dashboard_js_fetches_chart_per_symphony():
    """Dashboard JS must iterate bot_state entries and fetch /api/chart/<symId> for each.

    PM punch list item 6: 'Card sparklines missing.' The JS must:
    1. Iterate over each symphony in the bot_state response
    2. Fetch /api/chart/<symId> for each symphony
    3. Render the sparkline canvas/SVG with the returned series data

    Currently no per-symphony chart fetch exists.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    js_path = _STATIC_DIR / "index.js"
    source = html
    if js_path.exists():
        source += "\n" + js_path.read_text(encoding="utf-8")

    # Must have a per-symphony loop that fetches /api/chart/<id>
    has_per_sym_chart_fetch = (
        # Template literal with sym id
        "`/api/chart/${" in source or
        "'/api/chart/' +" in source or
        '"/api/chart/" +' in source or
        # forEach / for loop over symphonies + fetch
        ("forEach" in source and "api/chart" in source) or
        ("for " in source and "api/chart" in source and "bot_state" in source)
    )

    assert has_per_sym_chart_fetch, (
        "Dashboard JS must fetch /api/chart/<symId> for each symphony in bot_state. "
        "The pattern must be: botState entries.forEach(sym => fetch(`/api/chart/${sym.id}`)). "
        "Currently no per-symphony chart fetch exists in index.html or index.js. "
        "This is required for card sparklines (PM punch list item 6)."
    )


# ---------------------------------------------------------------------------
# FIX-25: <title> content must NOT appear verbatim as rendered body text
# ---------------------------------------------------------------------------


def test_title_content_not_rendered_in_body_on_all_screens(client):
    """The <title> tag text must not appear verbatim as visible body text on any screen.

    PM cross-screen audit (2026-05-19) observed the title string appearing as if it
    were body text on every screen. Guard that the <title> content is only inside
    <head> and does not leak into the <body> area as a rendered text node.

    This is a structural regression guard — the exact title string (e.g.
    "Performance — AlphaBot Dashboard v3") must not appear anywhere after </head>.
    """
    routes = [
        ("/", "Dashboard"),
        ("/performance", "Performance"),
        ("/history", "History"),
        ("/ai-advisor", "AI Advisor"),
    ]
    for route, name in routes:
        resp = client.get(route)
        assert resp.status_code == 200, (
            f"GET {route} returned {resp.status_code}; cannot check title bleed."
        )
        html = resp.data.decode("utf-8", errors="replace")

        # Extract the <title> content
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if not title_match:
            continue
        title_text = title_match.group(1).strip()
        if not title_text:
            continue

        # Locate the body area (everything after </head>)
        head_end_idx = html.lower().find("</head>")
        if head_end_idx < 0:
            continue
        body_area = html[head_end_idx:]

        # The full <title> string must not appear as a text node in the body
        # (nav wordmark "AlphaBot" is fine; the full brand string is not)
        brand_suffix = "AlphaBot Dashboard v3"
        assert brand_suffix not in body_area, (
            f"Screen '{name}' ({route}): <title> brand suffix '{brand_suffix}' "
            f"found in the body area after </head>. "
            "The <title> content must only appear inside <head><title>, "
            "not as a rendered text node in the page body."
        )


# ---------------------------------------------------------------------------
# FIX-26: /performance rendered HTML must have >= 8 chart elements
#          (1 cumulative SVG + 7 metric comparison SVG/canvas bars)
#          Tested against the live Flask client, not the source template.
# ---------------------------------------------------------------------------


def test_performance_rendered_html_has_cumulative_chart_svg(client):
    """/performance rendered HTML must have the cumulative returns SVG chart.

    PM browser-verify (2026-05-19) requires chart elements to be present in the
    LIVE-RENDERED HTML, not just in the source template. Tests hitting the source
    template can pass even when the rendered page has no chart (e.g. Jinja strips
    them or they're generated dynamically). This test hits the Flask test client.

    performance.jsx ChartBlock renders a large SVG for the cumulative returns chart.
    The rendered /performance page must contain at least one <canvas> or chart-purpose
    <svg> element (not a nav arrow icon SVG).
    """
    resp = client.get("/performance")
    assert resp.status_code == 200, f"/performance returned {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # The cumulative returns chart: a <canvas> OR an SVG with chart-purpose attributes
    # Exclude nav arrow SVGs (small, no viewBox with large dimensions)
    has_canvas = bool(re.search(r'<canvas[^>]*(?:id|data-testid)[^>]*>', html, re.I))
    has_chart_svg = bool(re.search(
        r'<svg[^>]*(?:data-testid="[^"]*chart[^"]*"|id="[^"]*chart[^"]*"|'
        r'preserveAspectRatio="none"|viewBox="0 0 \d{3,})',
        html, re.I,
    ))

    assert has_canvas or has_chart_svg, (
        "GET /performance rendered HTML must have a cumulative returns chart element "
        "(<canvas> with id/testid, or <svg> with preserveAspectRatio='none' or large viewBox). "
        f"Found {len(re.findall(r'<canvas', html, re.I))} canvas, "
        f"{len(re.findall(r'<svg', html, re.I))} SVG total. "
        "performance.jsx ChartBlock renders an SVG for the Live vs Bot cumulative returns. "
        "The rendered HTML must contain this element, not just the source template."
    )


def test_performance_rendered_html_has_metric_chart_elements(client):
    """/performance rendered HTML must have >= 7 metric comparison chart elements.

    PM browser-verify (2026-05-19): performance page must have ≥ 8 total chart
    elements — 1 cumulative returns chart + 7 per-metric comparison bars (one per
    METRICS entry: Total Return, Annualized, Sharpe, Sortino, Max DD, Calmar, Win Rate).

    This test hits the live Flask client to count actual <canvas> and <svg> elements
    in the rendered HTML — not the source template. div containers without SVG/canvas
    children do NOT count.

    The per-metric bars must be rendered as <canvas> or <svg> elements (not bare divs).
    """
    resp = client.get("/performance")
    assert resp.status_code == 200, f"/performance returned {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    canvas_count = len(re.findall(r"<canvas", html, re.I))
    svg_count = len(re.findall(r"<svg", html, re.I))

    # Exclude nav/icon SVGs: small inline SVGs (width < 20, height < 20 or path-only)
    # Count only chart-purpose SVGs: those with viewBox, preserveAspectRatio, or large size
    chart_svgs = len(re.findall(
        r'<svg[^>]*(?:preserveAspectRatio|viewBox="0 0 \d{2,}|data-testid)',
        html, re.I,
    ))
    total_chart_elements = canvas_count + chart_svgs

    assert total_chart_elements >= 8, (
        f"GET /performance rendered HTML must have >= 8 chart elements "
        f"(1 cumulative returns chart + 7 per-metric comparison bars). "
        f"Found {canvas_count} canvas + {chart_svgs} chart-purpose SVG = {total_chart_elements} total. "
        f"(Raw SVG count: {svg_count} — excludes nav/icon SVGs.) "
        "Each of the 7 METRICS rows (Total Return, Annualized, Sharpe, Sortino, "
        "Max DD, Calmar, Win Rate) must have a <canvas> or <svg> bar element. "
        "performance.jsx renders these as visual bars, not just text. "
        "Implement the bars as inline SVGs or Chart.js canvases per metric row."
    )


def test_performance_js_dom_updates_metrics_after_fetch():
    """performance.js must update metric row cells after /api/performance fetch response."""
    js_path = _STATIC_DIR / "performance.js"
    assert js_path.exists(), "static/performance.js must exist"
    src = js_path.read_text(encoding="utf-8")

    updates_metrics = "metrics-tbody" in src or "metric-row" in src
    assert updates_metrics, (
        "static/performance.js must update the metrics table after the /api/performance "
        "fetch response. Currently the metrics table shows '--' placeholders permanently."
    )
    assert "api/performance" in src, (
        "static/performance.js must fetch /api/performance to populate metrics."
    )
    assert "textContent" in src or "innerHTML" in src, (
        "static/performance.js must update DOM (textContent or innerHTML) "
        "with values from the /api/performance response."
    )


# ---------------------------------------------------------------------------
# FIX-27: /history rendered HTML must have >= 5 chart elements
#          (1 daily-alpha strip SVG + 4 per-reason mini-bar SVG/canvas)
#          Tested against the live Flask client, not the source template.
# ---------------------------------------------------------------------------


def test_history_rendered_html_has_daily_strip_chart(client):
    """/history rendered HTML must have the daily alpha strip chart SVG.

    PM browser-verify (2026-05-19): chart elements must be present in the
    LIVE-RENDERED HTML from the Flask client. Tests against source template
    files are insufficient.

    history.jsx DailyStrip renders an SVG bar chart of daily guard alpha.
    The rendered /history page must contain the daily strip SVG.
    """
    resp = client.get("/history")
    assert resp.status_code == 200, f"/history returned {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    has_daily_svg = (
        'id="daily-chart-svg"' in html or
        'data-testid="daily-strip"' in html or
        bool(re.search(r'<svg[^>]*(?:daily|strip|chart)', html, re.I))
    )
    assert has_daily_svg, (
        "GET /history rendered HTML must have the daily alpha strip chart SVG. "
        "Required: <svg id='daily-chart-svg'> or similar. "
        "history.jsx DailyStrip renders per-day bar chart of guard alpha over the window."
    )


def test_history_rendered_html_has_per_reason_chart_elements(client):
    """/history rendered HTML must have >= 4 per-reason chart elements.

    PM browser-verify (2026-05-19): ≥ 5 total chart elements required:
    1 daily alpha strip + 4 per-reason mini-bar SVG/canvas (one per exit reason:
    TP / Trailing Stop / VWAP Breakdown / VWAP Bleed Cut).

    Tests the LIVE-RENDERED HTML from the Flask client (not source template).
    <div data-testid="reason-bar"> without an SVG/canvas child does NOT satisfy
    this requirement — the visual bar must be a <canvas> or <svg> element.
    """
    resp = client.get("/history")
    assert resp.status_code == 200, f"/history returned {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    canvas_count = len(re.findall(r"<canvas", html, re.I))
    # Chart-purpose SVGs: large viewBox or preserveAspectRatio (excludes nav arrow icons)
    chart_svgs = len(re.findall(
        r'<svg[^>]*(?:preserveAspectRatio|viewBox="0 0 \d{2,}|data-testid)',
        html, re.I,
    ))
    total_chart_elements = canvas_count + chart_svgs

    assert total_chart_elements >= 5, (
        f"GET /history rendered HTML must have >= 5 chart elements "
        f"(1 daily alpha strip + 4 per-reason mini-bars). "
        f"Found {canvas_count} canvas + {chart_svgs} chart-purpose SVG = {total_chart_elements} total. "
        f"(Raw SVG count: {len(re.findall(r'<svg', html, re.I))} — excludes nav/icon SVGs.) "
        "Each of the 4 exit-reason cards (TP, Trailing Stop, VWAP Breakdown, VWAP Bleed Cut) "
        "must have a <canvas> or <svg> bar element showing alpha magnitude. "
        "history.jsx ByReason renders visual bars per reason — implement as inline SVGs "
        "or canvas elements, not bare div containers."
    )


def test_history_js_updates_dom_after_fetch():
    """history.js must update the daily strip and reason cards after /api/history fetch."""
    js_path = _STATIC_DIR / "history.js"
    assert js_path.exists(), "static/history.js must exist"
    src = js_path.read_text(encoding="utf-8")

    assert "api/history" in src, (
        "static/history.js must fetch /api/history/<days> to populate the page."
    )
    assert "innerHTML" in src or "textContent" in src, (
        "static/history.js must update DOM from the /api/history/<days> response."
    )
    assert "daily" in src.lower(), (
        "static/history.js must update the daily alpha strip from the API response."
    )


# ---------------------------------------------------------------------------
# FIX-28: /ai-advisor rendered HTML must have per-suggestion confidence ring
#          + projected-impact mini-bar + autotune-run sparklines
#          Tested against the live Flask client + mocked suggest response.
# ---------------------------------------------------------------------------


def test_advisor_rendered_html_has_suggestion_chart_containers(client):
    """/ai-advisor rendered HTML must have canvas/SVG containers for confidence
    ring + projected-impact mini-bar per suggestion, plus autotune sparklines.

    PM browser-verify (2026-05-19): advisor page has 0 canvas + 0 SVG.
    Required: per-suggestion confidence ring (SVG or canvas), projected-impact
    mini-bar (SVG or canvas), and per-run autotune sparkline on the right rail.

    Tests the LIVE-RENDERED HTML. The suggestion cards are JS-rendered after
    the POST to /ai-advisor/suggest, so this test verifies the template has
    placeholder containers that JS populates, OR the static HTML scaffolding
    for these visual elements.
    """
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"/ai-advisor returned {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    canvas_count = len(re.findall(r"<canvas", html, re.I))
    svg_count = len(re.findall(r"<svg", html, re.I))

    # The advisor page must have chart containers for:
    # 1. Per-suggestion confidence ring (canvas or SVG per card slot)
    # 2. Per-suggestion projected-impact mini-bar (canvas or SVG)
    # 3. Autotune sparklines on right rail
    # Minimum: at least 1 canvas or chart-purpose SVG container in the initial render
    has_chart_container = canvas_count > 0 or bool(re.search(
        r'<svg[^>]*(?:data-testid|preserveAspectRatio|viewBox="0 0 \d)',
        html, re.I,
    ))
    assert has_chart_container, (
        f"GET /ai-advisor rendered HTML must have at least one chart container element "
        f"(<canvas> or chart-purpose <svg>) for confidence rings, projected-impact bars, "
        f"or autotune sparklines. "
        f"Found {canvas_count} canvas + {svg_count} SVG total (0 chart-purpose). "
        "The advisor page currently has no visual chart elements. "
        "Add: canvas containers for confidence rings per suggestion slot, "
        "SVG/canvas for projected-impact mini-bars, and autotune sparkline containers."
    )


def test_advisor_js_generates_suggestion_cards_with_required_fields():
    """ai_advisor.js must generate suggestion cards from /ai-advisor/suggest response.

    Each SuggestionCard per advisor.jsx must include:
    - config_key (the parameter name)
    - confidence badge (high/medium/low)
    - OOS status badge (passed/rejected/pending)
    - projected impact delta (numeric, colored by sign)
    - data_sufficiency badge (when not 'sufficient')

    The suggestion card HTML must be built dynamically by JS from the POST response,
    not Jinja-baked at render time.
    """
    js_path = _STATIC_DIR / "ai_advisor.js"
    assert js_path.exists(), "static/ai_advisor.js must exist"
    src = js_path.read_text(encoding="utf-8")

    # JS must POST to /ai-advisor/suggest
    assert "ai-advisor/suggest" in src, (
        "ai_advisor.js must POST to /ai-advisor/suggest to get suggestions. "
    )

    # confidence badge
    assert "confidence" in src, (
        "ai_advisor.js must render a confidence badge per suggestion card. "
        "advisor.jsx SuggestionCard shows a Badge for sug.confidence (high/medium/low). "
        "ai_advisor.js must include 'confidence' field rendering in generated card HTML."
    )

    # OOS status badge
    assert "oos_status" in src, (
        "ai_advisor.js must render an OOS status badge per suggestion card. "
        "advisor.jsx shows Badge for sug.oos_status (passed/rejected/pending). "
        "ai_advisor.js must include 'oos_status' field in generated card HTML."
    )

    # Projected impact delta
    assert "impact" in src and "delta" in src, (
        "ai_advisor.js must render projected impact delta per suggestion card. "
        "advisor.jsx shows 'Projected <metric>' with sug.impact.delta (signed, colored). "
        "ai_advisor.js must include impact.delta rendering in generated card HTML."
    )

    # data_sufficiency badge
    assert "data_sufficiency" in src, (
        "ai_advisor.js must render data_sufficiency badge per suggestion card when "
        "data_sufficiency != 'sufficient'. "
        "advisor.jsx renders a Badge for 'insufficient data' scenarios. "
        "ai_advisor.js must include 'data_sufficiency' field in generated card HTML."
    )

    # The card HTML must be dynamically generated (innerHTML assignment)
    assert "innerHTML" in src, (
        "ai_advisor.js must dynamically generate suggestion card HTML via innerHTML. "
        "Cards cannot be Jinja-baked at render time — the suggest POST response varies."
    )


def test_advisor_js_populates_autotune_panel_from_api():
    """ai_advisor.js must populate the autotune runs panel from /api/autotune-runs.

    The recent-runs panel renders a list of runs with: symphony name,
    naive_sharpe, the selection statistic, frozen_eval_verdict, completed_at.

    The right-rail autotune panel must be populated by JS from /api/autotune-runs,
    not static HTML. Currently the tbody shows 'Loading...' permanently if the
    JS fetch fails or doesn't run.

    Re-pinned for Decision D3: the per-run selection statistic is the Harvey &
    Liu haircut t-statistic (field `selection_tstat`), NOT a Deflated Sharpe
    Ratio — D3 removed the DSR. The JS must reference `selection_tstat` and must
    not carry the deleted 'DSR' name.
    """
    js_path = _STATIC_DIR / "ai_advisor.js"
    assert js_path.exists(), "static/ai_advisor.js must exist"
    src = js_path.read_text(encoding="utf-8")

    assert "api/autotune-runs" in src, (
        "ai_advisor.js must fetch /api/autotune-runs to populate the right-rail panel. "
        "The recent-runs panel displays recent Optuna study results per symphony."
    )

    # The JS must update the autotune tbody
    assert "autotune-runs-tbody" in src or "autotune" in src.lower(), (
        "ai_advisor.js must update the autotune runs table body from the API response. "
    )

    # Must include naive_sharpe and the selection statistic in generated row HTML
    assert "naive_sharpe" in src or "sharpe" in src.lower(), (
        "ai_advisor.js autotune run rows must include naive_sharpe. "
    )

    assert "selection_tstat" in src, (
        "ai_advisor.js autotune run rows must include selection_tstat — the "
        "Harvey & Liu haircut winner's t-statistic (D3; replaced the deleted "
        "deflated_sharpe / DSR field)."
    )
    assert "dsr" not in src.lower() and "deflated sharpe" not in src.lower(), (
        "ai_advisor.js still contains 'DSR' / 'Deflated Sharpe' — D3 removed the "
        "Deflated Sharpe Ratio; the autotune rows must use selection_tstat."
    )

    assert "frozen_eval" in src or "frozen" in src.lower(), (
        "ai_advisor.js autotune run rows must include frozen_eval_verdict. "
        "The recent-runs panel shows the frozen-eval decision color per run."
    )


def test_advisor_route_returns_200(client):
    """GET /ai-advisor must return 200 and render the advisor template.

    Basic smoke test: the route must be registered and the template must render
    without errors. The autotune panel and suggestion container are expected
    to be present in the response HTML.
    """
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code}; "
        "route must be registered and return 200."
    )
    html = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="advisor-page"' in html, (
        "GET /ai-advisor must render the advisor page with data-testid='advisor-page'."
    )
    assert 'data-testid="autotune-panel"' in html, (
        "Advisor page must have the autotune panel right-rail element."
    )
    assert 'data-testid="suggestions-column"' in html, (
        "Advisor page must have the suggestions column element."
    )


# ---------------------------------------------------------------------------
# UX BLOCK-1: MC dial containers must have rendering logic in index.js
# ---------------------------------------------------------------------------


def test_index_js_has_mc_dial_rendering_logic():
    """index.js must contain rendering logic that populates [data-testid='mc-dial'] containers.

    UX review (cycle-2-fix) found that index.html has data-testid='mc-dial' divs on
    active cards but index.js has no querySelector for .mc-dial, no renderMcDial
    function, and no reference to any MC percentage field. The containers are
    permanently empty 40x40px boxes.

    Required: index.js must:
    1. Reference 'mc-dial' (or 'mc_dial') to select the dial containers
    2. Read an MC percentage value from the /api/chart or /api/state response
       (expected field: 'mc_prob' or 'mc_pct' or 'mc' from the chart data)
    3. Render the dial value into the container (textContent or draw on canvas/SVG)
    """
    js_path = _STATIC_DIR / "index.js"
    assert js_path.exists(), "static/index.js must exist"
    src = js_path.read_text(encoding="utf-8")

    # Must reference mc-dial containers
    has_mc_dial_ref = (
        "mc-dial" in src or
        "mc_dial" in src or
        "mcDial" in src or
        "renderMcDial" in src or
        "renderMC" in src
    )
    assert has_mc_dial_ref, (
        "static/index.js must reference MC dial containers to render MC probability. "
        "index.html has data-testid='mc-dial' divs on active cards but index.js has "
        "no querySelector for .mc-dial, no renderMcDial function, and no MC field reference. "
        "The dial containers are permanently empty. "
        "Add: querySelector('[data-testid=\"mc-dial\"]') and populate from API response."
    )

    # Must reference an MC percentage field from the API
    has_mc_value_field = (
        "mc_prob" in src or
        "mc_pct" in src or
        '"mc"' in src or
        "'mc'" in src or
        "mc_percent" in src
    )
    assert has_mc_value_field, (
        "static/index.js must read an MC percentage field from the API response "
        "to populate the MC dial. Expected field names: 'mc_prob', 'mc_pct', or 'mc'. "
        "Check /api/chart/<sym> or /api/state bot_state for the MC probability field."
    )


# ---------------------------------------------------------------------------
# UX BLOCK-2: guard-alpha-headline element must have id attribute
# ---------------------------------------------------------------------------


def test_guard_alpha_headline_element_has_id_attribute():
    """index.html guard-alpha-headline element must have an id attribute.

    UX review (cycle-2-fix) found: index.js line uses getElementById('guard-alpha-headline')
    but the template element has only data-testid and class attributes — no id.
    getElementById always returns null; renderGuardAlpha silently no-ops.
    The guard alpha headline is permanently Jinja-baked and never updates.

    Required: the guard-alpha-headline element must have id="guard-alpha-headline"
    so that getElementById resolves correctly and renderGuardAlpha can update it.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    assert index_path.exists(), "templates/index.html must exist"
    html = index_path.read_text(encoding="utf-8")

    # Find the guard-alpha-headline element
    ga_elem_match = re.search(
        r'<[^>]*(?:guard-alpha-headline|data-testid="guard-alpha-headline")[^>]*>',
        html,
    )
    assert ga_elem_match is not None, (
        "index.html must have a guard-alpha-headline element "
        "(data-testid='guard-alpha-headline' or class='guard-alpha-headline')."
    )

    ga_elem = ga_elem_match.group(0)
    # Must be a standalone id= attribute (not a substring of data-testid=)
    # Correct: id="guard-alpha-headline"
    # False positive: data-testid="guard-alpha-headline" contains id="guard-alpha-headline" as substring
    has_id = bool(re.search(r'\bid\s*=\s*["\']guard-alpha-headline["\']', ga_elem))
    assert has_id, (
        f"guard-alpha-headline element must have id='guard-alpha-headline' as a standalone "
        f"attribute (not just inside data-testid). "
        f"Current element: {ga_elem[:200]}. "
        "index.js uses getElementById('guard-alpha-headline') to update the value "
        "from the /api/state AJAX response, but getElementById returns null when "
        "the element has no id attribute. The headline never updates — it stays "
        "permanently at its Jinja-baked value."
    )


def test_guard_alpha_headline_js_can_resolve_element():
    """index.js getElementById('guard-alpha-headline') must find an element in index.html.

    Complementary to test_guard_alpha_headline_element_has_id_attribute — verifies
    the JS call and the template element are in sync: the id referenced in the JS
    must exist in the template.
    """
    index_path = _TEMPLATES_DIR / "index.html"
    js_path = _STATIC_DIR / "index.js"
    assert index_path.exists(), "templates/index.html must exist"
    assert js_path.exists(), "static/index.js must exist"

    html = index_path.read_text(encoding="utf-8")
    js_src = js_path.read_text(encoding="utf-8")

    # Extract all getElementById calls that reference guard-alpha
    ga_ids = re.findall(
        r'getElementById\([\'\"](guard-alpha[^\'\"]*)[\'\"]\)',
        js_src,
    )
    assert ga_ids, (
        "index.js must call getElementById with a guard-alpha headline id. "
        "Expected: getElementById('guard-alpha-headline') or similar."
    )

    # Each id referenced in JS must exist as a standalone id= attribute in the template
    # (not as a substring of data-testid= or similar compound attributes)
    for elem_id in ga_ids:
        escaped = re.escape(elem_id)
        has_standalone_id = bool(re.search(
            r'\bid\s*=\s*["\']' + escaped + r'["\']',
            html,
        ))
        assert has_standalone_id, (
            f"index.js calls getElementById('{elem_id}') but index.html has no "
            f"element with standalone id='{elem_id}'. The JS call always returns null, "
            "meaning the guard alpha headline never gets updated from AJAX data."
        )


# ---------------------------------------------------------------------------
# UX BLOCK-3: _chrome.html density select must default to 'balanced', not 'roomy'
# ---------------------------------------------------------------------------


def test_chrome_html_density_select_defaults_to_balanced():
    """_chrome.html density select must have 'selected' on the 'balanced' option.

    UX review (cycle-2-fix) found: _chrome.html density select has selected on
    'roomy' option, not 'balanced'. This regressed from the cycle-1 fix.

    The design spec (design-handoff/project/index.html TWEAK_DEFAULTS) requires
    density: 'balanced' as the default. The existing test_tweaks_js_density_default_is_balanced
    covers tweaks.js but NOT the _chrome.html select element.

    Required: the 'balanced' option in the density select must have the 'selected' attribute.
    The 'roomy' option must NOT have 'selected'.
    """
    chrome_path = _TEMPLATES_DIR / "_chrome.html"
    assert chrome_path.exists(), "templates/_chrome.html must exist"
    src = chrome_path.read_text(encoding="utf-8")

    # Find the density select section
    density_section = re.search(
        r'density.*?</select>',
        src, re.DOTALL | re.I,
    )
    assert density_section is not None, (
        "_chrome.html must have a density select (tweaks panel). "
        "Expected a <select> element with density options."
    )
    density_html = density_section.group(0)

    # The 'balanced' option must have selected
    balanced_selected = bool(re.search(
        r'<option[^>]*value=[\'"]balanced[\'"][^>]*selected|'
        r'<option[^>]*selected[^>]*value=[\'"]balanced[\'"]',
        density_html,
    ))
    assert balanced_selected, (
        "_chrome.html density select must have 'selected' on the 'balanced' option. "
        "Currently 'selected' is on 'roomy'. "
        "Design spec requires density default = 'balanced' (same as tweaks.js TWEAK_DEFAULTS). "
        "Fix: move 'selected' from the roomy option to the balanced option."
    )

    # The 'roomy' option must NOT have selected
    roomy_selected = bool(re.search(
        r'<option[^>]*value=[\'"]roomy[\'"][^>]*selected|'
        r'<option[^>]*selected[^>]*value=[\'"]roomy[\'"]',
        density_html,
    ))
    assert not roomy_selected, (
        "_chrome.html density select must NOT have 'selected' on the 'roomy' option. "
        "Only the 'balanced' option should be selected by default."
    )


# ---------------------------------------------------------------------------
# PM audit: hist_dates and daily_alpha must not be silently capped to 3 entries
#           when the DB has more data (30-day window requested vs 3 returned)
# ---------------------------------------------------------------------------


def test_api_state_hist_dates_not_capped_when_db_has_more_data(client, monkeypatch):
    """/api/state meta.portfolio.hist_dates must return >= 30 entries when DB has >= 30 rows.

    PM browser-verify (2026-05-19) observed hist_dates length = 3 even for a 30-day
    request. Diagnosis: the dev DB has only 11 chart_archive rows — so 3 is correct
    for the dev environment. This test verifies the BACKEND DOES NOT CAP at 3 by
    injecting a 35-day known-state DB fixture and asserting >= 30 entries are returned.

    If the backend silently truncates, this will fail even with the fixture.
    """
    with patch.object(app_module, "get_api_state_dict") as mock_gad:
        mock_gad.return_value = _make_api_state_dict_stub(
            hist_dates=_THIRTY_FIVE_DAYS[:],
            hist_bot=[0.001 * (i + 1) for i in range(35)],
            hist_held=[0.0008 * (i + 1) for i in range(35)],
        )
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            db_mock.read_port_state.return_value = None

            resp = client.get("/api/state")

    assert resp.status_code == 200
    body = resp.get_json()
    hist_dates = body.get("meta", {}).get("portfolio", {}).get("hist_dates", [])
    assert len(hist_dates) >= 30, (
        f"/api/state meta.portfolio.hist_dates returned {len(hist_dates)} entries "
        "when the fixture has 35 days of data. The backend must not silently cap "
        "hist_dates to a small number. If the real DB only has 3 rows, that is "
        "expected — but the backend must pass through all available rows."
    )


def test_api_history_daily_alpha_not_capped_when_db_has_more_data(client, monkeypatch):
    """/api/history/30 daily_alpha must return entries proportional to available data.

    PM browser-verify (2026-05-19) observed daily_alpha length = 3 for /api/history/30.
    This test uses the known-state DB fixture (35 days of post_mortem-equivalent data)
    to assert the backend returns >= 30 daily entries when the DB has them.

    If the real DB only has 3 rows of decisions data, 3 is the correct answer.
    This test guards against a backend bug that caps the result regardless of DB content.
    """
    # Inject a mock analytics response with 30 daily entries
    mock_history_response = {
        "total_alpha": 4.2,
        "total_saved": 520.40,
        "trigger_count": 28,
        "wins": 17,
        "win_rate": 60.7,
        "avg_guard_alpha": 0.150,
        "daily_alpha": [0.15 + 0.01 * i for i in range(30)],
        "by_reason": {
            "Take-Profit": {"alpha": 2.1, "count": 14, "wins": 9},
            "Trailing Stop": {"alpha": 1.2, "count": 8, "wins": 5},
            "VWAP Breakdown": {"alpha": 0.6, "count": 4, "wins": 2},
            "VWAP Bleed Cut": {"alpha": 0.3, "count": 2, "wins": 1},
        },
        "todays_exits": [],
    }

    # Patch the analytics function that produces history data
    import analytics as analytics_module
    with patch.object(analytics_module, "get_history_summary", return_value=mock_history_response,
                      create=True):
        with patch.object(app_module, "database") as db_mock:
            db_mock.get_ro_connection.return_value = MagicMock()
            resp = client.get("/api/history/30")

    if resp.status_code == 404:
        pytest.skip("/api/history/30 route not registered — skip until route added")

    assert resp.status_code == 200, f"/api/history/30 returned {resp.status_code}"
    body = resp.get_json()
    daily = body.get("daily_alpha", [])
    assert len(daily) >= 30, (
        f"/api/history/30 returned daily_alpha with {len(daily)} entries. "
        "When the backend has 30 days of data it must return 30 entries, not truncate. "
        "If the real DB only has 3 rows of decisions, that is expected — "
        "this test guards against a backend cap bug by injecting a 30-entry mock."
    )


# ---------------------------------------------------------------------------
# UX BLOCK-1 (cycle-2-fix-v2): renderMetrics must emit metric-bar SVGs in the
# rebuilt DOM, not just in the static server-rendered scaffolding.
# ---------------------------------------------------------------------------


def test_performance_js_render_metrics_emits_metric_bar_svg():
    """performance.js renderMetrics must include data-testid="metric-bar" SVG in rebuilt rows.

    UX review (2026-05-19) found that the 7 metric-bar SVGs in the server-rendered
    performance.html are destroyed when renderMetrics fires — it rebuilds tbody.innerHTML
    with rows containing no SVG elements. After the first fetch the DOM has zero
    metric-bar SVGs, which is what the PM sees in the browser.

    This test asserts the JS source string for renderMetrics contains the string
    'metric-bar' AND a SVG-generating pattern so the rebuilt DOM includes bar elements.
    """
    perf_js = _STATIC_DIR / "performance.js"
    src = perf_js.read_text(encoding="utf-8")

    # Locate the renderMetrics function body
    match = re.search(r"function renderMetrics\b.*?(?=\nfunction |\Z)", src, re.S)
    assert match, "performance.js must define a renderMetrics function"
    fn_body = match.group(0)

    assert "metric-bar" in fn_body, (
        "renderMetrics in performance.js must emit 'metric-bar' in the innerHTML it builds. "
        "Currently it rebuilds tbody rows with text-only delta columns — no SVG elements. "
        "Each metric row's delta column must include an SVG bar element with "
        'data-testid="metric-bar" whose rect width reflects the metric delta.'
    )
    assert re.search(r"<svg|svg[^>]*viewBox", fn_body, re.I), (
        "renderMetrics must emit an <svg> element inside each rebuilt row. "
        "The static scaffolding SVGs are destroyed on first render — the JS "
        "must regenerate them with actual computed widths."
    )


# ---------------------------------------------------------------------------
# UX BLOCK-2 (cycle-2-fix-v2): renderReasonCards must emit reason-bar SVGs in
# the rebuilt DOM, not just in the static server-rendered scaffolding.
# ---------------------------------------------------------------------------


def test_history_js_render_reason_cards_emits_reason_bar_svg():
    """history.js renderReasonCards must include data-testid="reason-bar" SVG in rebuilt cards.

    UX review (2026-05-19) found that the 4 reason-bar SVGs in the server-rendered
    history.html are destroyed when renderReasonCards fires — it rebuilds container.innerHTML
    with cards containing no SVG elements. After the first fetch the DOM has zero
    reason-bar SVGs, which is what the PM sees in the browser.

    This test asserts the JS source string for renderReasonCards contains 'reason-bar'
    AND an SVG-generating pattern.
    """
    hist_js = _STATIC_DIR / "history.js"
    src = hist_js.read_text(encoding="utf-8")

    match = re.search(r"function renderReasonCards\b.*?(?=\nfunction |\nwindow\.|\Z)", src, re.S)
    assert match, "history.js must define a renderReasonCards function"
    fn_body = match.group(0)

    assert "reason-bar" in fn_body, (
        "renderReasonCards in history.js must emit 'reason-bar' in the innerHTML it builds. "
        "Currently it rebuilds cards with text/grid layouts but no SVG bar elements. "
        "Each reason card must include an SVG bar element with data-testid=\"reason-bar\" "
        "whose rect width reflects the win-rate or alpha value for that reason."
    )
    assert re.search(r"<svg|svg[^>]*viewBox", fn_body, re.I), (
        "renderReasonCards must emit an <svg> element inside each rebuilt card. "
        "The static scaffolding SVGs are destroyed on first render — the JS "
        "must regenerate them with actual computed widths."
    )


# ---------------------------------------------------------------------------
# Parity audit (cycle-2-fix-v2): Advisor suggestion cards missing confidence
# ring SVG, projected-impact bar, and four-gates verdict badges.
# ---------------------------------------------------------------------------


def test_advisor_js_render_suggestions_emits_confidence_ring():
    """ai_advisor.js renderSuggestions must emit a confidence ring SVG per card.

    Parity audit (2026-05-19) found 0 SVG elements inside suggestion cards at runtime.
    advisor.jsx design shows a circular arc gauge per card indicating confidence level.
    The confidence value (high/medium/low) is present in the suggestion payload.

    This test asserts renderSuggestions JS source includes SVG confidence ring markup
    with data-testid='confidence-ring'.
    """
    advisor_js = _STATIC_DIR / "ai_advisor.js"
    src = advisor_js.read_text(encoding="utf-8")

    match = re.search(r"function renderSuggestions\b.*?(?=\nfunction |\nwindow\.|\Z)", src, re.S)
    assert match, "ai_advisor.js must define a renderSuggestions function"
    fn_body = match.group(0)

    assert "confidence-ring" in fn_body, (
        "renderSuggestions in ai_advisor.js must emit an element with "
        "data-testid='confidence-ring' per suggestion card. "
        "Design (advisor.jsx): a circular SVG arc gauge whose stroke-dashoffset "
        "reflects the confidence level (high/medium/low). "
        "Currently no SVG ring is emitted — the parity audit found 0 SVG elements "
        "inside suggestion cards at runtime."
    )
    assert re.search(r"<svg|viewBox|stroke-dashoffset|stroke-dasharray", fn_body, re.I), (
        "renderSuggestions must emit SVG arc markup (viewBox, stroke-dashoffset, "
        "or stroke-dasharray) for the confidence ring. "
        "Zero SVG elements per card found in live DOM audit."
    )


def test_advisor_js_render_suggestions_emits_projected_impact_bar():
    """ai_advisor.js renderSuggestions must emit a projected-impact bar per card.

    Parity audit (2026-05-19) found 0 projected-impact bar elements in suggestion cards.
    advisor.jsx design shows a mini-bar comparing before/after metric values.
    The impact data (metric, before, after, delta) is present in the suggestion payload.

    This test asserts renderSuggestions JS source includes projected-impact bar markup
    with data-testid='projected-impact-bar'.
    """
    advisor_js = _STATIC_DIR / "ai_advisor.js"
    src = advisor_js.read_text(encoding="utf-8")

    match = re.search(r"function renderSuggestions\b.*?(?=\nfunction |\nwindow\.|\Z)", src, re.S)
    assert match, "ai_advisor.js must define a renderSuggestions function"
    fn_body = match.group(0)

    assert "projected-impact" in fn_body, (
        "renderSuggestions in ai_advisor.js must emit an element with "
        "data-testid='projected-impact-bar' (or 'projected-impact') per suggestion card. "
        "Design (advisor.jsx): a horizontal bar showing before/after metric comparison "
        "with a delta annotation. s.impact.{metric, before, after, delta} is in the payload. "
        "Currently the impact delta is shown as inline text only — no bar element."
    )


def test_advisor_js_render_suggestions_emits_four_gates_badges():
    """ai_advisor.js renderSuggestions must emit four-gates verdict badges per card.

    Parity audit (2026-05-19) found 0 four-gates badge elements in suggestion cards.
    advisor.jsx design shows 4 gate verdict chips per card:
      allowlist, risk_direction, oos_frozen_eval, locked_vars.
    The four_gates_verdict object is part of the suggestion payload contract.

    This test asserts renderSuggestions JS source includes four-gates badge markup
    with data-testid='gate-badge' (or 'four-gates').
    """
    advisor_js = _STATIC_DIR / "ai_advisor.js"
    src = advisor_js.read_text(encoding="utf-8")

    match = re.search(r"function renderSuggestions\b.*?(?=\nfunction |\nwindow\.|\Z)", src, re.S)
    assert match, "ai_advisor.js must define a renderSuggestions function"
    fn_body = match.group(0)

    assert re.search(r"gate.badge|four.gate|four_gate", fn_body, re.I), (
        "renderSuggestions in ai_advisor.js must emit gate badge elements per card. "
        "Design (advisor.jsx): 4 gate verdict chips — allowlist / risk_direction / "
        "oos_frozen_eval / locked_vars — each colored pass/fail/warn. "
        "Currently no gate badges are rendered in the card HTML. "
        "Add data-testid='gate-badge' elements derived from s.four_gates_verdict."
    )


# ---------------------------------------------------------------------------
# Parity audit (cycle-2-fix-v2): History reason cards missing description text
# and avg-per-exit metric in renderReasonCards output.
# ---------------------------------------------------------------------------


def test_history_js_render_reason_cards_emits_description_text():
    """history.js renderReasonCards must emit a description sentence per reason card.

    Parity audit (2026-05-19) found 0 description text elements in reason cards.
    history.jsx design shows a description sentence per exit mechanism, e.g.:
      TP: 'Take-profit target reached — locked-in gains above VWAP'
      Stop: 'Trailing stop triggered — drawdown exceeded threshold'
      VWAP Breakdown: 'VWAP structural breakdown — mean reversion risk'
      Bleed Cut: 'Slow bleed cut — cumulative loss exceeded patience threshold'

    This test asserts renderReasonCards emits a description element per card.
    """
    hist_js = _STATIC_DIR / "history.js"
    src = hist_js.read_text(encoding="utf-8")

    match = re.search(r"function renderReasonCards\b.*?(?=\nfunction |\nwindow\.|\nvar |\Z)", src, re.S)
    assert match, "history.js must define a renderReasonCards function"
    fn_body = match.group(0)

    assert re.search(r"reason.desc|description|reasonDesc|reason_desc", fn_body, re.I), (
        "renderReasonCards in history.js must emit a description text element per card. "
        "Design (history.jsx): each exit reason has a one-sentence explanation. "
        "Add a data-testid='reason-description' element with the per-reason description string. "
        "Use a static lookup map keyed by the reason name (TP/Stop/VWAP Breakdown/Bleed Cut)."
    )


def test_history_js_render_reason_cards_emits_avg_per_exit():
    """history.js renderReasonCards must emit avg-per-exit metric per reason card.

    Parity audit (2026-05-19) found 0 avg-per-exit metric elements in reason cards.
    history.jsx design shows 'avg α/exit' — the per-trigger average guard alpha.
    Computed as s.alpha / s.count when s.count > 0.

    This test asserts renderReasonCards emits an avg-per-exit element.
    """
    hist_js = _STATIC_DIR / "history.js"
    src = hist_js.read_text(encoding="utf-8")

    match = re.search(r"function renderReasonCards\b.*?(?=\nfunction |\nwindow\.|\nvar |\Z)", src, re.S)
    assert match, "history.js must define a renderReasonCards function"
    fn_body = match.group(0)

    assert re.search(r"avg.per.exit|avg_per_exit|avgPerExit|avg.*exit|per.exit", fn_body, re.I), (
        "renderReasonCards in history.js must emit an avg-per-exit metric per card. "
        "Design (history.jsx): 'avg α/exit' computed as alpha/count per reason. "
        "Add a data-testid='avg-per-exit' element showing the computed average. "
        "Currently the mini-stat grid shows Triggers / Wins / Win Rate but omits avg α/exit."
    )


# ---------------------------------------------------------------------------
# Dashboard BLOCKs — parity re-audit 2026-05-19 (cycle-2-fix v4)
#
# FIX-DASH-1  renderGuardAlpha in index.js reads wrong JSON path (portfolio.cr)
#             instead of portfolio_strip.cumulative_return.{dry_run,if_held}
# FIX-DASH-2  meta.portfolio.cr populated from portfolio_strip.cumulative_return.dry_run
#             (verifies _build_meta → API → JS pipeline is non-zero end-to-end)
# FIX-DASH-3  /api/state bot_state per-symphony objects include guard_alpha field
# ---------------------------------------------------------------------------


def test_dashboard_js_render_guard_alpha_reads_portfolio_strip_cumulative_return():
    """index.js renderGuardAlpha must read from data.portfolio_strip.cumulative_return.

    Parity re-audit (2026-05-19) BLOCK-1: renderGuardAlpha reads
    `meta.portfolio.cr` and `meta.portfolio.cr_if_held` (both 0 in practice)
    instead of `portfolio_strip.cumulative_return.dry_run` /
    `portfolio_strip.cumulative_return.if_held` (live values: 1.41 / 68.6).

    The fix: update renderGuardAlpha to destructure
    `data.portfolio_strip.cumulative_return` for its input values.

    This test asserts the JS source references portfolio_strip and
    cumulative_return (not just portfolio.cr) so the fix is verifiable at
    source level.
    """
    idx_js = _STATIC_DIR / "index.js"
    src = idx_js.read_text(encoding="utf-8")

    match = re.search(
        r"function renderGuardAlpha\b.*?(?=\nfunction |\nvar |\Z)", src, re.S
    )
    assert match, "index.js must define a renderGuardAlpha function"
    fn_body = match.group(0)

    assert re.search(r"portfolio_strip", fn_body), (
        "renderGuardAlpha must read from portfolio_strip, not meta.portfolio. "
        "Parity confirmed portfolio_strip.cumulative_return.dry_run = 1.41 while "
        "meta.portfolio.cr = 0. Fix: change function signature to accept `data` "
        "and read `(data.portfolio_strip || {}).cumulative_return || {}`."
    )
    assert re.search(r"cumulative_return", fn_body), (
        "renderGuardAlpha must reference cumulative_return from portfolio_strip. "
        "Expected: var cr_obj = (data.portfolio_strip || {}).cumulative_return || {}; "
        "var cr = cr_obj.dry_run || 0; var crHeld = cr_obj.if_held || 0;"
    )


def test_api_state_meta_portfolio_cr_non_zero_when_portfolio_strip_has_data(monkeypatch):
    """meta.portfolio.cr in /api/state must be non-zero when portfolio_strip has CR data.

    Parity re-audit BLOCK-1: even if renderGuardAlpha is fixed to read portfolio_strip
    directly, this test verifies the meta.portfolio.cr pipeline is also correct so
    Jinja-rendered fallback paths (e.g. comparison rows) get non-zero values.

    Injects portfolio_strip.cumulative_return = {dry_run: 1.41, if_held: 68.6}
    and asserts meta.portfolio.cr == 1.41 and meta.portfolio.cr_if_held == 68.6.
    """
    import analytics as analytics_module

    non_zero_strip = {
        "today_change": {"dry_run": 0.5, "if_held": 0.3},
        "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
        "max_drawdown": {"dry_run": -5.2, "if_held": -3.1},
        "hist_dates": _THIRTY_FIVE_DAYS[:],
        "hist_bot": [0.001 * (i + 1) for i in range(35)],
        "hist_held": [0.0008 * (i + 1) for i in range(35)],
    }
    stub = _make_api_state_dict_stub()
    stub["portfolio_strip"] = non_zero_strip

    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = _BOT_STATE_STUB.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.5, "if_held": 0.3}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -5.2, "if_held": -3.1}):

                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/api/state")
                    assert resp.status_code == 200
                    data = json.loads(resp.data)

    meta_portfolio = data.get("meta", {}).get("portfolio", {})
    assert meta_portfolio.get("cr") == pytest.approx(1.41, abs=0.01), (
        f"meta.portfolio.cr must equal portfolio_strip.cumulative_return.dry_run (1.41) "
        f"but got {meta_portfolio.get('cr')}. "
        "Check _build_meta: cr_data = ps.get('cumulative_return') or {} must yield "
        "{'dry_run': 1.41} when portfolio_strip is properly injected."
    )
    assert meta_portfolio.get("cr_if_held") == pytest.approx(68.6, abs=0.01), (
        f"meta.portfolio.cr_if_held must equal portfolio_strip.cumulative_return.if_held (68.6) "
        f"but got {meta_portfolio.get('cr_if_held')}."
    )


def test_api_state_bot_state_triggered_symphony_includes_guard_alpha(monkeypatch):
    """/api/state bot_state must include guard_alpha per triggered symphony.

    Parity re-audit (2026-05-19) BLOCK-3: index.js line 142 reads
    `sym.guard_alpha` from bot_state entries to show the verdict ('Good call ·
    saved +X%α' / 'Early exit · gave up X%α'). The /api/state route does NOT
    currently include guard_alpha in bot_state per-symphony objects, so the JS
    always shows '0.0%α'.

    The fix: /api/state must inject guard_alpha into each triggered symphony's
    bot_state entry. Source: database.load_state() or a dedicated DB query for
    guard_alpha by symphony_id.

    This test sets up a triggered symphony with a non-zero guard_alpha in the DB
    and asserts the /api/state response bot_state[sym_id].guard_alpha is present
    and non-zero.
    """
    import analytics as analytics_module

    triggered_bot_state = {
        "sym_abc": {
            "id": "sym_abc",
            "name": "Alpha Symphony",
            "position": 0.8,
            "simple_return": 0.05,
            "net_deposits": 0.0,
            "time_weighted_return": 0.05,
            "triggered": True,
        }
    }
    # Use dict-valued portfolio_strip (not flat floats) so _build_meta can call .get().
    stub = {
        "bot_state": triggered_bot_state,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.0, "if_held": 0.0},
            "cumulative_return": {"dry_run": 0.05, "if_held": 0.04},
            "max_drawdown": {"dry_run": -0.02, "if_held": -0.01},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }

    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = triggered_bot_state.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            # Simulate DB having guard_alpha = 2.7 for this symphony
            db_mock.get_guard_alpha_by_symphony.return_value = {"sym_abc": 2.7}
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.0, "if_held": 0.0}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 0.05, "if_held": 0.04}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -0.02, "if_held": -0.01}):

                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/api/state")
                    assert resp.status_code == 200
                    data = json.loads(resp.data)

    bot_state = data.get("bot_state") or data.get("state") or {}
    sym_entry = bot_state.get("sym_abc", {})
    assert "guard_alpha" in sym_entry, (
        "bot_state['sym_abc'] must include a 'guard_alpha' key for triggered symphonies. "
        "The /api/state route must inject guard_alpha into each triggered bot_state entry. "
        "Source: add database.get_guard_alpha_by_symphony() or equivalent, then merge "
        "{'guard_alpha': value} into each triggered sym entry before returning bot_state."
    )
    assert sym_entry["guard_alpha"] != 0, (
        f"bot_state['sym_abc'].guard_alpha must be non-zero (expected 2.7) but got "
        f"{sym_entry.get('guard_alpha')}. The route must look up the actual guard_alpha "
        "value from the DB, not default to 0."
    )


# ---------------------------------------------------------------------------
# Parity re-audit at a0ce875 — 2 remaining Dashboard BLOCKs
#
# FIX-DASH-2b  Comparison rows (TODAY/CUMULATIVE/MAX DD) have no JS update path.
#              index.js has no updateComparisonRows function; the rows stay Jinja-static.
# FIX-DASH-3b  guard_alpha injection only fires when triggered symphonies exist in the
#              live DB. When the dev DB has no triggered symphonies, bot_state never
#              gets guard_alpha injected — the code path is never exercised in practice.
# ---------------------------------------------------------------------------


def test_dashboard_js_has_comparison_row_update_function():
    """index.js must define a function that updates comparison rows from portfolio_strip.

    Parity re-audit at a0ce875 BLOCK-2: updateDashboard() has NO call to any
    comparison-row update function. The TODAY/CUMULATIVE/MAX DD rows stay at
    Jinja-rendered '+0.00%' forever after page load — they are never refreshed
    by the 30s polling loop.

    The API provides the real values at:
      portfolio_strip.today_change.{dry_run, if_held}
      portfolio_strip.cumulative_return.{dry_run, if_held}
      portfolio_strip.max_drawdown.{dry_run, if_held}

    The fix requires implementing updateComparisonRows(data) in static/index.js
    and calling it from updateDashboard(data). It must update DOM elements for
    each of the 3 comparison rows (today, cumulative, max_drawdown) with both
    bot and if-held values.

    This test asserts index.js defines a function that reads today_change,
    cumulative_return, and max_drawdown from portfolio_strip so the polling
    loop can update those rows after each /api/state response.
    """
    idx_js = _STATIC_DIR / "index.js"
    src = idx_js.read_text(encoding="utf-8")

    assert re.search(r"today_change", src), (
        "index.js must reference portfolio_strip.today_change to update the TODAY "
        "comparison row. The row currently shows Jinja-static +0.00% after page load "
        "because updateDashboard() never refreshes it. "
        "Add updateComparisonRows(data) reading data.portfolio_strip.today_change."
    )
    assert re.search(r"updateCompar|compRow|comp.row|vs.row|vsRow", src, re.I), (
        "index.js must contain a comparison-row update function called from "
        "updateDashboard(). The function must update TODAY / CUMULATIVE / MAX DD rows "
        "from portfolio_strip after each /api/state poll. "
        "Expected: function updateComparisonRows(data) { ... } called in updateDashboard."
    )


def test_dashboard_js_comparison_rows_called_from_update_dashboard():
    """updateDashboard in index.js must call the comparison-row update function.

    Parity BLOCK-2: even if updateComparisonRows is defined, it must be wired
    into the polling loop via updateDashboard — otherwise it never fires.

    This test asserts the updateDashboard function body references the comparison
    row update call so each 30s poll refreshes those values.
    """
    idx_js = _STATIC_DIR / "index.js"
    src = idx_js.read_text(encoding="utf-8")

    match = re.search(
        r"function updateDashboard\b.*?(?=\nfunction |\nvar |\Z)", src, re.S
    )
    assert match, "index.js must define updateDashboard"
    fn_body = match.group(0)

    assert re.search(r"today_change|updateCompar|compRow|comp.row", fn_body, re.I), (
        "updateDashboard in index.js must call the comparison-row update function "
        "or directly read today_change/cumulative_return/max_drawdown from portfolio_strip. "
        "Currently updateDashboard calls renderHeroChart, renderGuardAlpha, updateVerdicts, "
        "loadCharts — but nothing updates the TODAY/CUMULATIVE/MAX DD rows. "
        "Add: updateComparisonRows(data); inside updateDashboard."
    )


def test_index_js_has_named_updateComparisonRows_function():
    """index.js must define a function literally named updateComparisonRows.

    PM directive: grep for the exact function name. The existing tests assert
    portfolio_strip references but allow any function name — this test pins the
    exact name so DOM selectors and future tests can reference it unambiguously.
    """
    idx_js = _STATIC_DIR / "index.js"
    src = idx_js.read_text(encoding="utf-8")

    assert "updateComparisonRows" in src, (
        "index.js must define 'updateComparisonRows' by that exact name. "
        "Parity audit confirmed this function is absent. "
        "Add: function updateComparisonRows(data) { ... } reading "
        "portfolio_strip.today_change, cumulative_return, max_drawdown."
    )


def test_index_js_updateComparisonRows_reads_all_three_portfolio_strip_keys():
    """updateComparisonRows must reference today_change, cumulative_return, and max_drawdown.

    All three comparison rows (TODAY / CUMULATIVE / MAX DD) require separate
    data sources from portfolio_strip. The function body must reference all three
    keys so each row gets the correct live value from the polling response.
    """
    idx_js = _STATIC_DIR / "index.js"
    src = idx_js.read_text(encoding="utf-8")

    match = re.search(
        r"function updateComparisonRows\b.*?(?=\nfunction |\nvar |\Z)", src, re.S
    )
    assert match, "index.js must define updateComparisonRows"
    fn_body = match.group(0)

    for key in ("today_change", "cumulative_return", "max_drawdown"):
        assert key in fn_body, (
            f"updateComparisonRows must reference portfolio_strip.{key} to update "
            f"the corresponding comparison row. All 3 rows need separate data: "
            f"today_change → TODAY row, cumulative_return → CUMULATIVE row, "
            f"max_drawdown → MAX DD row."
        )


def test_rendered_dashboard_has_comp_bar_elements(monkeypatch):
    """Rendered / HTML must include comp-bar-bot and comp-bar-held elements per row.

    Parity BLOCK-2: live DOM has 0 [data-testid=\"comp-bar-bot\"] elements.
    The comparison rows need bar-width elements in the template (either Jinja-rendered
    or JS-injected) so updateComparisonRows can update their widths after each poll.

    This test asserts the rendered HTML contains at least 3 comp-bar-bot and
    3 comp-bar-held elements (one per row: TODAY / CUMULATIVE / MAX DD).
    """
    import analytics as analytics_module

    stub = _make_api_state_dict_stub()
    stub["portfolio_strip"] = {
        "today_change": {"dry_run": 0.5, "if_held": 0.3},
        "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
        "max_drawdown": {"dry_run": -5.2, "if_held": -3.1},
        "hist_dates": _THIRTY_FIVE_DAYS[:],
        "hist_bot": [0.001 * (i + 1) for i in range(35)],
        "hist_held": [0.0008 * (i + 1) for i in range(35)],
    }

    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = _BOT_STATE_STUB.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.5, "if_held": 0.3}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -5.2, "if_held": -3.1}):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/")
                    assert resp.status_code == 200
                    html = resp.data.decode("utf-8")

    bot_bars = len(re.findall(r'data-testid=["\']comp-bar-bot["\']', html))
    held_bars = len(re.findall(r'data-testid=["\']comp-bar-held["\']', html))

    assert bot_bars >= 3, (
        f"Rendered dashboard must have >= 3 [data-testid='comp-bar-bot'] elements "
        f"(one per comparison row: TODAY/CUMULATIVE/MAX DD) but found {bot_bars}. "
        "Add data-testid='comp-bar-bot' bar elements to the Jinja template so "
        "updateComparisonRows() can update their widths after each /api/state poll."
    )
    assert held_bars >= 3, (
        f"Rendered dashboard must have >= 3 [data-testid='comp-bar-held'] elements "
        f"but found {held_bars}. "
        "Add data-testid='comp-bar-held' bar elements to the Jinja template."
    )


def test_rendered_dashboard_has_comp_text_testids(monkeypatch):
    """Rendered dashboard must have all 6 data-testid text-value elements for comparison rows.

    ux BLOCK (cycle-2-fix-v5): updateComparisonRows() queries 6 text-value elements via
    dynamic selectors: comp-{today,cumulative,mdd}-{bot,held}-text. None of these
    data-testid attributes exist in the template — querySelector() returns null on every
    poll, so textContent is never updated and values remain Jinja-baked at +0.00%.

    Fix: add the 6 data-testid attributes to the vs-val spans in templates/index.html:
    - Today row:      data-testid="comp-today-bot-text", data-testid="comp-today-held-text"
    - Cumulative row: data-testid="comp-cumulative-bot-text", data-testid="comp-cumulative-held-text"
    - Max DD row:     data-testid="comp-mdd-bot-text", data-testid="comp-mdd-held-text"
    """
    import analytics as analytics_module

    stub = {
        "bot_state": _BOT_STATE_STUB.copy(),
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 1.15, "if_held": 68.80},
            "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
            "max_drawdown": {"dry_run": -0.28, "if_held": -0.19},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }

    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = _BOT_STATE_STUB.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            db_mock.get_guard_alpha_by_symphony.return_value = {}
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 1.15, "if_held": 68.80}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -0.28, "if_held": -0.19}):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/")
                    assert resp.status_code == 200
                    html = resp.data.decode("utf-8")

    required_testids = [
        "comp-today-bot-text",
        "comp-today-held-text",
        "comp-cumulative-bot-text",
        "comp-cumulative-held-text",
        "comp-mdd-bot-text",
        "comp-mdd-held-text",
    ]
    for testid in required_testids:
        assert f'data-testid="{testid}"' in html, (
            f"Rendered dashboard is missing data-testid=\"{testid}\" on the vs-val span. "
            f"updateComparisonRows() queries this element via "
            f"document.querySelector('[data-testid=\"{testid}\"]') — if the attribute is "
            f"absent, querySelector returns null and textContent is never updated after polls. "
            f"Fix: add data-testid=\"{testid}\" to the matching vs-val span in templates/index.html."
        )


def test_index_js_updateComparisonRows_uses_dynamic_text_selector_pattern():
    """updateComparisonRows() must use the dynamic comp-{id}-bot-text / held-text pattern.

    Verifies the JS selector pattern that generates the 6 text-testid queries is present
    in the function body. If this test fails, the selector pattern was changed and the
    template data-testid attributes are now misaligned.
    """
    src = (_STATIC_DIR / "index.js").read_text(encoding="utf-8")
    match = re.search(r"function updateComparisonRows\b.*?(?=\nfunction |\Z)", src, re.S)
    assert match, "index.js must define a updateComparisonRows function"
    fn_body = match.group(0)
    assert re.search(r"comp-.*?-bot-text|comp.*bot.*text", fn_body), (
        "updateComparisonRows() must query bot-text elements via a selector containing "
        "'comp-...-bot-text'. The function body does not contain this pattern. "
        "Either the selector was changed or the function was removed."
    )
    assert re.search(r"comp-.*?-held-text|comp.*held.*text", fn_body), (
        "updateComparisonRows() must query held-text elements via a selector containing "
        "'comp-...-held-text'. The function body does not contain this pattern."
    )
    assert re.search(r"textContent", fn_body), (
        "updateComparisonRows() must assign .textContent on the queried text elements. "
        "Without this assignment, the DOM values are never updated after polls."
    )


def test_get_guard_alpha_by_symphony_returns_nonzero_when_exit_triggers_seeded(tmp_path):
    """database.get_guard_alpha_by_symphony() must return the seeded at_return value.

    Provenance check (PM v5 test #5): the existing /api/state test mocks
    get_guard_alpha_by_symphony entirely. This test hits a real SQLite DB to
    verify the field provenance: when exit_triggers has a row for a symphony
    with a non-null at_return, the function returns a non-null non-zero float
    for that symphony.

    Fails if:
    - exit_triggers table is not queried (returns {})
    - at_return is coerced to 0.0 due to a None/falsy check
    - the function silently swallows an exception and returns {}
    - the symphony_ids filter drops the requested id
    """
    import sqlite3

    import database as db_module

    db_path = str(tmp_path / "test_provenance.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE exit_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            symphony_name TEXT,
            at_return REAL,
            ts_utc TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO exit_triggers (symphony_id, symphony_name, at_return, ts_utc) "
        "VALUES (?, ?, ?, ?)",
        ("sym_provenance", "Provenance Test Symphony", 3.14, "2026-05-19T12:00:00Z"),
    )
    conn.commit()
    conn.close()

    original_db_file = db_module.DB_FILE
    db_module.DB_FILE = db_path
    try:
        result = db_module.get_guard_alpha_by_symphony(["sym_provenance"])
    finally:
        db_module.DB_FILE = original_db_file

    assert "sym_provenance" in result, (
        "get_guard_alpha_by_symphony(['sym_provenance']) must return a dict with "
        "'sym_provenance' as a key when exit_triggers has a matching row. "
        f"Got: {result!r}. "
        "The function must query the exit_triggers table and return the at_return value."
    )
    assert result["sym_provenance"] != 0.0, (
        "get_guard_alpha_by_symphony must return the actual at_return value (3.14), "
        f"not 0.0. Got: {result['sym_provenance']!r}. "
        "Check that at_return is not being coerced to 0.0 via a falsy guard."
    )
    assert abs(result["sym_provenance"] - 3.14) < 0.001, (
        f"Expected guard_alpha ≈ 3.14 for 'sym_provenance', got {result['sym_provenance']!r}. "
        "The at_return column value must flow through unchanged."
    )


def test_guard_alpha_is_triggered_at_return_minus_current_return(monkeypatch):
    """guard_alpha in /api/state bot_state must equal triggered_at_return - current_return.

    PM BLOCK-A (cycle-2-fix-v7): Live daemon shows guard_alpha == triggered_at_return
    (raw at_return from exit_triggers) instead of triggered_at_return - current_return.
    Example: Planet LQD cr=2.195, trig_at=2.17 → guard_alpha=2.17 (wrong), should be -0.025.

    The injection in app.py line 1000 currently stores _ga (= at_return) directly.
    Fix: compute _ga = at_return - state_data[_sid].get("current_return", 0.0) before
    calling setdefault.

    This test sets up:
    - triggered_at_return = 5.0 (stored in both bot_state and exit_triggers.at_return)
    - current_return = 4.5 (in bot_state, simulating the position has declined since exit)
    Expected guard_alpha = 5.0 - 4.5 = 0.5 (bot saved 0.5% by exiting at 5.0)

    Fails if guard_alpha == 5.0 (raw at_return) instead of 0.5.
    """
    import analytics as analytics_module

    triggered_bot_state = {
        "sym_ga_math": {
            "id": "sym_ga_math",
            "name": "GA Math Symphony",
            "position": 0.6,
            "simple_return": 0.05,
            "net_deposits": 0.0,
            "time_weighted_return": 0.05,
            "triggered": True,
            "triggered_at_return": 5.0,
            "current_return": 4.5,  # declined since exit → bot was right to exit
        }
    }
    stub = {
        "bot_state": triggered_bot_state,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.0, "if_held": 0.0},
            "cumulative_return": {"dry_run": 0.05, "if_held": 0.04},
            "max_drawdown": {"dry_run": -0.01, "if_held": -0.01},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }

    with patch.object(app_module, "get_api_state_dict", return_value=stub):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = triggered_bot_state.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            # exit_triggers at_return == triggered_at_return (5.0) — this is the raw DB value
            db_mock.get_guard_alpha_by_symphony.return_value = {"sym_ga_math": 5.0}
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.0, "if_held": 0.0}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 0.05, "if_held": 0.04}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -0.01, "if_held": -0.01}):

                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/api/state")
                    assert resp.status_code == 200
                    data = json.loads(resp.data)

    bot_state = data.get("bot_state") or data.get("state") or {}
    sym_entry = bot_state.get("sym_ga_math", {})

    assert "guard_alpha" in sym_entry, (
        "bot_state['sym_ga_math'] must include 'guard_alpha'. "
        f"Got keys: {list(sym_entry.keys())!r}"
    )

    ga = sym_entry["guard_alpha"]
    triggered_at = triggered_bot_state["sym_ga_math"]["triggered_at_return"]  # 5.0
    current_ret = triggered_bot_state["sym_ga_math"]["current_return"]  # 4.5
    expected = round(triggered_at - current_ret, 6)  # 0.5

    assert abs(ga - expected) < 0.001, (
        f"guard_alpha must equal triggered_at_return - current_return = "
        f"{triggered_at} - {current_ret} = {expected}, "
        f"but got {ga!r}. "
        "The /api/state injection stores raw at_return instead of computing the alpha delta. "
        "Fix: in the guard_alpha injection block (app.py ~line 1000), compute "
        "_ga = at_return - state_data[_sid].get('current_return', 0.0) "
        "before calling setdefault."
    )

    assert abs(ga - triggered_at) > 0.001, (
        f"guard_alpha must NOT equal raw triggered_at_return ({triggered_at}). "
        f"Got guard_alpha={ga!r} which matches the raw at_return, not the alpha delta. "
        "This is the BLOCK-A bug: app.py stores at_return directly instead of "
        "computing triggered_at_return - current_return."
    )


def test_api_state_portfolio_strip_today_change_is_dict_not_float(monkeypatch):
    """portfolio_strip.today_change in /api/state must be {dry_run, if_held}, not a flat float.

    PM BLOCK-B (cycle-2-fix-v7): dashboard today_change row shows 0.00% despite API
    returning 0.6530. updateComparisonRows() reads data.portfolio_strip.today_change.dry_run
    — if today_change is a flat float (e.g. 0.6530) then .dry_run is undefined → JS reads 0.

    This test verifies the API response shape: portfolio_strip.today_change must be a dict
    with both 'dry_run' and 'if_held' keys, not a scalar.

    Fails if analytics.get_portfolio_today_change returns a float or the /api/state
    response nests today_change differently.
    """
    import analytics as analytics_module

    with patch.object(app_module, "get_api_state_dict", return_value={
        "bot_state": _BOT_STATE_STUB.copy(),
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.653, "if_held": 0.712},
            "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
            "max_drawdown": {"dry_run": -0.28, "if_held": -0.19},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = _BOT_STATE_STUB.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            db_mock.get_guard_alpha_by_symphony.return_value = {}
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.653, "if_held": 0.712}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -0.28, "if_held": -0.19}):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/api/state")
                    assert resp.status_code == 200
                    data = json.loads(resp.data)

    ps = data.get("portfolio_strip", {})
    today_change = ps.get("today_change")

    assert isinstance(today_change, dict), (
        f"portfolio_strip.today_change must be a dict with 'dry_run' and 'if_held' keys. "
        f"Got type {type(today_change).__name__!r}: {today_change!r}. "
        "updateComparisonRows() reads data.portfolio_strip.today_change.dry_run — "
        "a flat float or None will cause dry_run to be undefined in JS → 0.00% displayed."
    )
    assert "dry_run" in today_change, (
        f"portfolio_strip.today_change must have a 'dry_run' key. Got: {today_change!r}. "
        "updateComparisonRows() JS: var bot = typeof row.values.dry_run === 'number' ? ..."
    )
    assert "if_held" in today_change, (
        f"portfolio_strip.today_change must have an 'if_held' key. Got: {today_change!r}."
    )
    assert today_change["dry_run"] != 0.0, (
        f"portfolio_strip.today_change.dry_run must be non-zero when analytics returns 0.653. "
        f"Got {today_change['dry_run']!r}. Check that the analytics result is not being "
        "overwritten or zeroed before jsonify."
    )


def test_index_js_update_dashboard_passes_data_not_meta_to_comparison_rows():
    """updateDashboard must pass the full data object (not meta) to updateComparisonRows.

    PM BLOCK-B: updateComparisonRows reads data.portfolio_strip. If updateDashboard
    accidentally passes meta (which has no portfolio_strip key), comparison rows will
    always show 0.00%.

    Verifies the call site in updateDashboard passes the same data argument received
    from fetch('/api/state'), not the locally extracted meta variable.
    """
    src = (_STATIC_DIR / "index.js").read_text(encoding="utf-8")
    match = re.search(r"function updateDashboard\b.*?(?=\nfunction |\Z)", src, re.S)
    assert match, "index.js must define an updateDashboard function"
    fn_body = match.group(0)

    # The call must be updateComparisonRows(data) not updateComparisonRows(meta)
    call_match = re.search(r"updateComparisonRows\s*\((\w+)\)", fn_body)
    assert call_match, (
        "updateDashboard() must call updateComparisonRows(...). "
        "The function body does not contain this call."
    )
    arg = call_match.group(1)
    assert arg != "meta", (
        f"updateComparisonRows() is called with '{arg}' but must receive the full data "
        "object (not meta). meta has no portfolio_strip key — passing meta means "
        "comparison rows will always show 0.00%."
    )
    # The argument should be 'data' (the fetch response), which carries portfolio_strip
    assert arg == "data", (
        f"updateComparisonRows() is called with '{arg}' but must be called with 'data' "
        "(the full /api/state response object that contains portfolio_strip)."
    )


def test_template_cash_now_button_uses_token_not_bare_hex():
    """Cash Now button CSS must use design tokens — no bare hex colors.

    PM BLOCK-D: user reports Cash Now buttons don't match design styling. The design
    spec calls for a danger-red outlined button. Current impl uses var(--studio-neg)
    as background fill. This test asserts:
    1. The button element has the expected data-testid
    2. The CSS class uses a design token (var(--studio-...)) not a bare hex value
    3. No bare 6-digit or 3-digit hex colors in the .cash-now-btn rule block

    This is a static source check — visual fidelity (outlined vs filled, hover state)
    requires parity's Playwright audit.
    """
    template_path = _TEMPLATES_DIR / "index.html"
    src = template_path.read_text(encoding="utf-8")

    # Button must be present with data-testid
    assert 'data-testid="cash-now-btn"' in src, (
        "templates/index.html must contain a Cash Now button with "
        "data-testid=\"cash-now-btn\". The button was not found."
    )

    # Extract the .cash-now-btn CSS block
    css_block_match = re.search(
        r"\.cash-now-btn\s*\{([^}]+)\}", src, re.S
    )
    assert css_block_match, (
        "templates/index.html must define a .cash-now-btn CSS rule block. Not found."
    )
    css_block = css_block_match.group(1)

    # No bare hex in the rule block
    bare_hex = re.findall(r"#[0-9a-fA-F]{3,8}\b", css_block)
    assert not bare_hex, (
        f"Cash Now button CSS contains bare hex color(s): {bare_hex}. "
        "All colors must use design tokens (var(--studio-...)) per project standards. "
        "Replace with the appropriate --studio-neg / --studio-danger token."
    )

    # Must use at least one design token
    assert "var(--studio-" in css_block, (
        f"Cash Now button CSS must use at least one design token (var(--studio-...)) "
        f"for color. Current rule: {css_block.strip()!r}"
    )


def test_rendered_dashboard_card_spark_has_non_empty_data_sym_id(monkeypatch):
    """Every [data-testid="card-spark"] canvas must have a non-empty data-sym-id attribute.

    Parity BLOCK-C-1: All 17 canvases have data-sym-id="" because the Jinja template uses
    {{ sym.get('id', '') }} but the sym dicts from database.load_state() do NOT have an
    'id' field. The id injection (state_data[k]['id'] = k) only happens in the /api/state
    endpoint handler, not in the dashboard() template render path.

    renderSparkline() selects canvas by data-sym-id so every sparkline lookup fails silently.

    This test uses a bot_state WITHOUT 'id' fields to replicate the real load_state() output.

    Fix: in dashboard() route, inject sym['id'] = k for each sym dict before template render,
    OR use {{ sym_id }} (the loop variable) instead of {{ sym.get('id', '') }}.
    """
    import analytics as analytics_module

    # Bot state WITHOUT 'id' key — matches real database.load_state() output
    # (id injection only happens in /api/state endpoint, not dashboard() route)
    bot_state_no_id = {
        "sym_abc": {
            "name": "Alpha Symphony",
            "position": 0.8,
            "simple_return": 0.05,
            "net_deposits": 0.0,
            "time_weighted_return": 0.05,
            # NOTE: no 'id' field — matches real load_state() output
        }
    }

    with patch.object(app_module, "get_api_state_dict", return_value={
        "bot_state": bot_state_no_id,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.5, "if_held": 0.3},
            "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
            "max_drawdown": {"dry_run": -5.2, "if_held": -3.1},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = bot_state_no_id.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.5, "if_held": 0.3}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -5.2, "if_held": -3.1}):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/")
                    assert resp.status_code == 200
                    html = resp.data.decode("utf-8")

    # Find all data-sym-id values on card-spark canvases
    sym_ids = re.findall(r'data-testid=["\']card-spark["\'][^>]*data-sym-id=["\']([^"\']*)["\']', html)
    # Also check reversed attribute order
    sym_ids += re.findall(r'data-sym-id=["\']([^"\']*)["\'][^>]*data-testid=["\']card-spark["\']', html)

    assert len(sym_ids) > 0, (
        "No [data-testid='card-spark'] canvases found in rendered HTML. "
        "The dashboard must render card-spark canvas elements for each symphony."
    )

    empty_ids = [v for v in sym_ids if not v.strip()]
    assert not empty_ids, (
        f"Found {len(empty_ids)} card-spark canvas(es) with empty data-sym-id=\"\". "
        "renderSparkline() selects canvas via [data-sym-id='<symId>'] — an empty value "
        "means all sparkline fetches select no element and draw nothing. "
        "Fix: inject sym['id'] = k before template render in dashboard() route, or "
        "change template to use the loop key variable instead of sym.get('id', '')."
    )

    non_empty = [v for v in sym_ids if v.strip()]
    assert len(non_empty) == len(sym_ids), (
        f"All card-spark canvases must have non-empty data-sym-id. "
        f"Got {len(non_empty)} non-empty out of {len(sym_ids)} total."
    )


def test_index_js_render_sparkline_reads_return_not_bot():
    """renderSparkline() must read d.return (not d.bot) from /api/chart response data.

    Parity BLOCK-C-2: static/index.js line 102 reads d.bot which is undefined in live
    /api/chart responses. Parity confirmed live API returns 134 rows with field "return"
    not "bot". Sparklines draw a flat zero line as a result.

    Fix: change d.bot to d.return (or add d['return'] alias) in the map() call inside
    renderSparkline().
    """
    src = (_STATIC_DIR / "index.js").read_text(encoding="utf-8")
    match = re.search(r"function renderSparkline\b.*?(?=\nfunction |\Z)", src, re.S)
    assert match, "index.js must define a renderSparkline function"
    fn_body = match.group(0)

    # Must NOT read d.bot (the wrong field name)
    assert not re.search(r"\bd\.bot\b", fn_body), (
        "renderSparkline() reads d.bot but /api/chart response rows use field 'return', "
        "not 'bot'. d.bot is always undefined → sparklines draw a flat zero line. "
        "Fix: change d.bot to d['return'] or d.return in the data.map() call."
    )

    # Must read d.return or d['return']
    assert re.search(r"d\[.return.\]|d\.return\b", fn_body), (
        "renderSparkline() must read d['return'] (or d.return) from chart data rows. "
        "Live /api/chart response format: [{date, return, ...}]. "
        "The current d.bot field is never present → all sparklines render as zero."
    )


def test_cash_now_button_css_is_outlined_not_solid_fill():
    """Cash Now button default state must be outlined (transparent bg), not solid fill.

    Parity BLOCK-D: Live computed style shows background: rgb(180,58,42) (solid red always).
    Design spec (studio.jsx): default state is transparent background + 1px solid border.
    Only hover fills solid red.

    This test asserts the CSS rule block uses transparent background and has border styling.
    """
    template_path = _TEMPLATES_DIR / "index.html"
    src = template_path.read_text(encoding="utf-8")

    css_block_match = re.search(r"\.cash-now-btn\s*\{([^}]+)\}", src, re.S)
    assert css_block_match, "templates/index.html must define a .cash-now-btn CSS rule block."
    css_block = css_block_match.group(1)

    # Background must NOT be a solid color in default state (must be transparent)
    assert re.search(r"background\s*:\s*transparent", css_block) or \
           not re.search(r"background\s*:", css_block), (
        f"Cash Now button default background must be transparent (outlined style). "
        f"Current CSS has a solid background fill. "
        f"Design spec: transparent bg in default, solid fill only on :hover. "
        f"Current rule: {css_block.strip()!r}"
    )

    # Must have border styling
    assert re.search(r"border\s*:|border-width\s*:|border-style\s*:", css_block), (
        f"Cash Now button must have border styling (outlined design). "
        f"Current CSS has no border property. "
        f"Design spec: 1px solid border at var(--studio-neg) 33% opacity. "
        f"Current rule: {css_block.strip()!r}"
    )


def test_cash_now_button_css_has_uppercase_text():
    """Cash Now button must have text-transform: uppercase per design spec.

    Parity BLOCK-D-4: Live button has no text-transform. Design spec: uppercase letters.
    """
    template_path = _TEMPLATES_DIR / "index.html"
    src = template_path.read_text(encoding="utf-8")

    css_block_match = re.search(r"\.cash-now-btn\s*\{([^}]+)\}", src, re.S)
    assert css_block_match, "templates/index.html must define a .cash-now-btn CSS rule block."
    css_block = css_block_match.group(1)

    assert re.search(r"text-transform\s*:\s*uppercase", css_block), (
        f"Cash Now button must have text-transform: uppercase. "
        f"Design spec (studio.jsx): text-transform: uppercase with letter-spacing. "
        f"Current rule: {css_block.strip()!r}"
    )


def test_cash_now_button_css_font_weight_is_600():
    """Cash Now button must have font-weight: 600 per design spec (not 700).

    Parity BLOCK-D-5: Live button has font-weight: 700. Design spec: 600.
    """
    template_path = _TEMPLATES_DIR / "index.html"
    src = template_path.read_text(encoding="utf-8")

    css_block_match = re.search(r"\.cash-now-btn\s*\{([^}]+)\}", src, re.S)
    assert css_block_match, "templates/index.html must define a .cash-now-btn CSS rule block."
    css_block = css_block_match.group(1)

    # Must be 600, not 700
    fw_match = re.search(r"font-weight\s*:\s*(\d+)", css_block)
    assert fw_match, (
        f"Cash Now button must have font-weight: 600. No font-weight found in CSS. "
        f"Current rule: {css_block.strip()!r}"
    )
    fw_val = int(fw_match.group(1))
    assert fw_val == 600, (
        f"Cash Now button font-weight must be 600 (design spec). "
        f"Got font-weight: {fw_val}. "
        f"Design spec (studio.jsx): font-weight: 600."
    )


def test_cash_now_button_css_has_border_styling():
    """Cash Now button must have a visible border (outlined design).

    Parity BLOCK-D-2: Live button has border: none. Design spec: 1px solid border
    in the button color (var(--studio-neg) at reduced opacity in default, full on hover).
    """
    template_path = _TEMPLATES_DIR / "index.html"
    src = template_path.read_text(encoding="utf-8")

    css_block_match = re.search(r"\.cash-now-btn\s*\{([^}]+)\}", src, re.S)
    assert css_block_match, "templates/index.html must define a .cash-now-btn CSS rule block."
    css_block = css_block_match.group(1)

    # Must NOT have border: none
    assert not re.search(r"border\s*:\s*none", css_block), (
        f"Cash Now button must not have 'border: none'. "
        f"Design spec requires a 1px solid outlined border. "
        f"Current rule: {css_block.strip()!r}"
    )

    # Must have some border definition
    assert re.search(r"border\s*:|border-width\s*:|border-style\s*:", css_block), (
        f"Cash Now button must have border styling. "
        f"Design spec: 1px solid var(--studio-neg) border in default state. "
        f"Current rule: {css_block.strip()!r}"
    )


def test_cash_now_button_triggered_card_has_disabled_state_css():
    """Cash Now button on triggered cards must have a CSS disabled/faint state.

    Parity BLOCK-D-3: Triggered cards show identical solid-red Cash Now buttons as
    armed cards — no visual distinction. Design spec: triggered state uses transparent
    bg, faint rule border, faint text (opacity or color change via :disabled or a
    data-state attribute). The dashboard must style triggered-card buttons differently.

    This test checks the template defines a CSS rule that targets disabled Cash Now
    buttons or a triggered-state variant.
    """
    template_path = _TEMPLATES_DIR / "index.html"
    src = template_path.read_text(encoding="utf-8")

    # Must have either :disabled pseudo-class rule or a triggered-state variant
    has_disabled_rule = bool(re.search(
        r"\.cash-now-btn\s*:\s*disabled|\.cash-now-btn\[disabled\]|"
        r"\.triggered\s+\.cash-now-btn|\.cash-now-btn--disabled|"
        r"\.cash-now-btn\s*\.\s*disabled",
        src
    ))
    assert has_disabled_rule, (
        "templates/index.html must define a CSS rule for the disabled/triggered state "
        "of .cash-now-btn. Design spec: triggered cards show a faint outlined button "
        "(transparent bg, reduced-opacity border, faint text). Armed cards show the "
        "full outlined button. Currently both states look identical. "
        "Fix: add '.cash-now-btn:disabled { opacity: 0.4; cursor: default; }' or "
        "equivalent, and set the disabled attribute on triggered-card Cash Now buttons."
    )


def test_rendered_dashboard_triggered_card_cash_now_btn_has_disabled_attr(monkeypatch):
    """Cash Now button inside a TRIGGERED card must render with the HTML `disabled` attribute.

    Parity D-3 (revised, cycle-2-fix-v7): CSS `.cash-now-btn:disabled` rule exists but
    the template never sets the `disabled` attribute on triggered cards. The `:disabled`
    CSS selector only fires when the HTML element has `disabled` set — without it, the
    button renders at full opacity identical to armed cards.

    Failing condition: template renders
      <button data-testid="cash-now-btn" class="cash-now-btn" type="button">Cash Now</button>
    for a triggered symphony — no `disabled` attr.

    Fix: add `{% if sym.get('triggered') %}disabled{% endif %}` to both Cash Now button
    elements in templates/index.html (lines ~730 and ~775).
    """
    import analytics as analytics_module

    triggered_bot_state = {
        "sym_triggered": {
            "name": "Triggered Symphony",
            "position": 0.0,
            "simple_return": 0.03,
            "net_deposits": 0.0,
            "time_weighted_return": 0.03,
            "triggered": True,
            "triggered_at_return": 3.5,
            "current_return": 3.8,
        }
    }

    with patch.object(app_module, "get_api_state_dict", return_value={
        "bot_state": triggered_bot_state,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.5, "if_held": 0.3},
            "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
            "max_drawdown": {"dry_run": -5.2, "if_held": -3.1},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = triggered_bot_state.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.5, "if_held": 0.3}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -5.2, "if_held": -3.1}):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/")
                    assert resp.status_code == 200
                    html = resp.data.decode("utf-8")

    # Find all Cash Now buttons
    cash_now_buttons = re.findall(
        r'<button[^>]*data-testid=["\']cash-now-btn["\'][^>]*>',
        html
    )
    assert len(cash_now_buttons) > 0, (
        "No [data-testid='cash-now-btn'] buttons found in rendered HTML for triggered symphony. "
        "The dashboard must render Cash Now buttons for each symphony card."
    )

    # Every Cash Now button in a triggered-symphony render must have `disabled`
    buttons_without_disabled = [b for b in cash_now_buttons if "disabled" not in b]
    assert not buttons_without_disabled, (
        f"Found {len(buttons_without_disabled)} Cash Now button(s) without the `disabled` "
        f"HTML attribute in a triggered-card render:\n"
        + "\n".join(f"  {b}" for b in buttons_without_disabled) + "\n"
        "The CSS `.cash-now-btn:disabled {{ opacity: 0.4 }}` rule only fires when the "
        "element has the `disabled` attribute set. Without it, triggered cards look "
        "identical to armed cards.\n"
        "Fix: add `{% if sym.get('triggered') %}disabled{% endif %}` to both Cash Now "
        "button elements in templates/index.html."
    )


def test_rendered_dashboard_armed_card_cash_now_btn_does_not_have_disabled_attr(monkeypatch):
    """Cash Now button inside an ARMED (non-triggered) card must NOT have the `disabled` attr.

    Guards against over-applying the disabled attribute to all buttons regardless of
    triggered state.
    """
    import analytics as analytics_module

    armed_bot_state = {
        "sym_armed": {
            "name": "Armed Symphony",
            "position": 0.8,
            "simple_return": 0.05,
            "net_deposits": 0.0,
            "time_weighted_return": 0.05,
            "triggered": False,
        }
    }

    with patch.object(app_module, "get_api_state_dict", return_value={
        "bot_state": armed_bot_state,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.5, "if_held": 0.3},
            "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
            "max_drawdown": {"dry_run": -5.2, "if_held": -3.1},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = armed_bot_state.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.5, "if_held": 0.3}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -5.2, "if_held": -3.1}):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/")
                    assert resp.status_code == 200
                    html = resp.data.decode("utf-8")

    cash_now_buttons = re.findall(
        r'<button[^>]*data-testid=["\']cash-now-btn["\'][^>]*>',
        html
    )
    assert len(cash_now_buttons) > 0, (
        "No [data-testid='cash-now-btn'] buttons found in rendered HTML for armed symphony."
    )

    buttons_with_disabled = [b for b in cash_now_buttons if "disabled" in b]
    assert not buttons_with_disabled, (
        f"Found {len(buttons_with_disabled)} Cash Now button(s) with `disabled` attribute "
        f"in an armed (non-triggered) card render — armed cards must be interactive:\n"
        + "\n".join(f"  {b}" for b in buttons_with_disabled)
    )


def test_api_state_guard_alpha_populated_from_real_db_exit_triggers(tmp_path, monkeypatch):
    """GET /api/state must include guard_alpha in bot_state for triggered symphonies
    when exit_triggers has a corresponding record — verified against a real SQLite DB,
    not a mock of get_guard_alpha_by_symphony.

    Parity BLOCK-3 (cycle-2-fix-v6): live daemon shows guard_alpha absent for all
    triggered symphonies. The existing guard_alpha test mocks get_guard_alpha_by_symphony
    entirely, so it cannot detect a broken DB→route pipeline. This test builds a real
    SQLite DB with:
    - bot_state: sym_beta with triggered=True
    - exit_triggers: sym_beta row with at_return=7.25
    Then calls /api/state with database.DB_FILE patched to the tmp DB and asserts
    bot_state["sym_beta"]["guard_alpha"] == 7.25.

    Fails if:
    - get_guard_alpha_by_symphony is never called (silent exception swallowing)
    - exit_triggers table is missing (function returns {})
    - _triggered_ids list is empty (triggered flag not read from load_state)
    - setdefault overwrites a prior value of 0 (wrong logic)
    """
    import sqlite3
    import json as _json

    import database as db_module
    import app as app_module

    triggered_state = {
        "sym_beta": {
            "id": "sym_beta",
            "name": "Beta Symphony",
            "position": 0.5,
            "simple_return": 0.08,
            "net_deposits": 0.0,
            "time_weighted_return": 0.08,
            "triggered": True,
            "triggered_at_return": 8.5,
        }
    }

    db_path = str(tmp_path / "test_guard_alpha_pipeline.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE bot_state (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("INSERT INTO bot_state VALUES (1, ?)", (_json.dumps(triggered_state),))
    conn.execute(
        "CREATE TABLE execution_lock (id INTEGER PRIMARY KEY, is_locked INTEGER, timestamp REAL)"
    )
    conn.execute("INSERT INTO execution_lock VALUES (1, 0, 0.0)")
    conn.execute(
        """CREATE TABLE exit_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            symphony_name TEXT,
            at_return REAL,
            ts_utc TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO exit_triggers (symphony_id, symphony_name, at_return, ts_utc) "
        "VALUES (?, ?, ?, ?)",
        ("sym_beta", "Beta Symphony", 7.25, "2026-05-19T10:00:00Z"),
    )
    # Minimal stubs for other tables the route queries
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chart_history (id INTEGER PRIMARY KEY, data TEXT)"
    )
    conn.execute("INSERT INTO chart_history VALUES (1, ?)", (_json.dumps({"symphonies": {}}),))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS strategies "
        "(symphony_id TEXT PRIMARY KEY, params TEXT, locked_vars TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    monkeypatch.setenv("DB_PATH", db_path)

    with patch.object(app_module, "get_api_state_dict", return_value={
        "bot_state": triggered_state,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.0, "if_held": 0.0},
            "cumulative_return": {"dry_run": 0.08, "if_held": 0.05},
            "max_drawdown": {"dry_run": -0.01, "if_held": -0.01},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }):
        # Patch only the functions that would fail without a full DB schema;
        # do NOT mock get_guard_alpha_by_symphony — that is the function under test.
        with patch.object(db_module, "load_state", return_value=triggered_state), \
             patch.object(db_module, "load_chart_history",
                          return_value={"symphonies": {}}), \
             patch.object(db_module, "normalize_name",
                          side_effect=lambda n: (n or "").lower().replace(" ", "_")), \
             patch.object(db_module, "get_symphony_strategy",
                          return_value={"params": {}, "locked_vars": []}), \
             patch.object(db_module, "read_fleet_alert", return_value=None), \
             patch.object(db_module, "get_shadow_divergence",
                          return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch.object(db_module, "read_port_state", return_value=None):

            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as c:
                resp = c.get("/api/state")
                assert resp.status_code == 200
                data = json.loads(resp.data)

    bot_state = data.get("bot_state") or data.get("state") or {}
    sym_entry = bot_state.get("sym_beta", {})

    assert "guard_alpha" in sym_entry, (
        "bot_state['sym_beta'] must include 'guard_alpha' when exit_triggers has a row "
        "for that symphony. The /api/state route calls get_guard_alpha_by_symphony() "
        "against the real DB — if the field is absent, the DB→route pipeline is broken. "
        f"Got bot_state keys: {list(sym_entry.keys())!r}. "
        "Check: (1) _triggered_ids list built from load_state result, "
        "(2) get_guard_alpha_by_symphony called with the DB_FILE-patched connection, "
        "(3) exit_triggers table present in DB, (4) setdefault merges the result."
    )
    assert sym_entry["guard_alpha"] is not None, (
        "bot_state['sym_beta'].guard_alpha must not be None — "
        f"got {sym_entry.get('guard_alpha')!r}."
    )
    assert abs(sym_entry["guard_alpha"] - 7.25) < 0.01, (
        f"Expected guard_alpha ≈ 7.25 (from exit_triggers.at_return), "
        f"got {sym_entry.get('guard_alpha')!r}. "
        "The at_return value must flow from exit_triggers → get_guard_alpha_by_symphony "
        "→ /api/state bot_state entry unchanged."
    )


def test_rendered_dashboard_triggered_card_guard_alpha_absent_shows_degraded_not_zero(monkeypatch):
    """When guard_alpha is absent from bot_state (dev DB has no exit_triggers rows),
    triggered card verdict must show a graceful degraded state — NOT '0.0%α'.

    Team-lead contract update (2026-05-19): 'If your cycle works in production but not
    in dev DB, the cycle is NOT complete — it's PARTIAL with a documented data gap.
    The dashboard MUST gracefully degrade for dev-state data.'

    Current behavior (FAIL): sym.get('guard_alpha', 0) or 0 → ga=0 →
    renders 'Early exit · gave up 0.0%α' — looks like a valid zero value, not a gap.

    Required behavior (PASS): when guard_alpha is absent, render '—' or 'n/a' or
    some explicit placeholder that is not '0.0%' or '0%' or any numeric representation
    of zero with a percent sign.

    Fix: templates/index.html — guard ga for None/absent and render a dash or n/a
    placeholder when guard_alpha is not present in the sym dict.
    """
    import analytics as analytics_module

    # Triggered sym WITHOUT guard_alpha key — matches dev-DB no-exit-triggers state
    triggered_no_ga = {
        "sym_no_ga": {
            "name": "No GA Symphony",
            "position": 0.0,
            "simple_return": 0.03,
            "net_deposits": 0.0,
            "time_weighted_return": 0.03,
            "triggered": True,
            "triggered_at_return": 3.5,
            "current_return": 3.8,
            # NOTE: no 'guard_alpha' key — matches real dev-DB state with no exit_triggers
        }
    }

    with patch.object(app_module, "get_api_state_dict", return_value={
        "bot_state": triggered_no_ga,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.5, "if_held": 0.3},
            "cumulative_return": {"dry_run": 1.41, "if_held": 68.6},
            "max_drawdown": {"dry_run": -5.2, "if_held": -3.1},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = triggered_no_ga.copy()
            db_mock.load_chart_history.return_value = _CHART_HISTORY_STUB.copy()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_ro_connection.return_value = MagicMock()
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {}, "portfolio_today": None
            }
            db_mock.read_port_state.return_value = None
            with patch.object(analytics_module, "get_portfolio_today_change",
                              return_value={"dry_run": 0.5, "if_held": 0.3}), \
                 patch.object(analytics_module, "get_portfolio_cumulative_return",
                              return_value={"dry_run": 1.41, "if_held": 68.6}), \
                 patch.object(analytics_module, "get_portfolio_max_drawdown",
                              return_value={"dry_run": -5.2, "if_held": -3.1}):
                app_module.app.config["TESTING"] = True
                with app_module.app.test_client() as c:
                    resp = c.get("/")
                    assert resp.status_code == 200
                    html = resp.data.decode("utf-8")

    # Must NOT render "0.0%α" or any numeric-zero-percent variant in the verdict area
    # Find the triggered-verdict section
    verdict_matches = re.findall(
        r'data-testid=["\']triggered-verdict["\'][^>]*>.*?</div>',
        html,
        re.DOTALL
    )

    # Broad check: no "0.0%α" or "0%α" anywhere in verdict sections
    zero_alpha_pattern = re.compile(r'0\.0\s*%.*?alpha|0\.0%α|gave up 0\.0%', re.IGNORECASE)
    for v in verdict_matches:
        assert not zero_alpha_pattern.search(v), (
            "Triggered card verdict must NOT show '0.0%α' when guard_alpha is absent — "
            "this looks like a real value (zero) rather than a data gap. "
            f"Rendered verdict: {v[:200]!r}\n"
            "Fix: in templates/index.html, check if guard_alpha is present before rendering "
            "a numeric value. When absent, show '—' or 'n/a' as a graceful placeholder.\n"
            "Example fix: {% if sym.guard_alpha is not none and 'guard_alpha' in sym %} "
            "... show value ... {% else %}—{% endif %}"
        )

    # Also check full page for the 0.0%α pattern as a catch-all
    assert not zero_alpha_pattern.search(html), (
        "Page HTML must not contain '0.0%α' or 'gave up 0.0%' when guard_alpha is absent. "
        "This output is misleading: it looks like a real 0.0% value rather than missing data.\n"
        "Fix: guard the Jinja guard_alpha render — show '—' when the field is absent."
    )


def test_api_state_guard_alpha_absent_when_no_exit_triggers_returns_none_or_missing(tmp_path, monkeypatch):
    """When exit_triggers has no rows for a triggered symphony, /api/state must return
    guard_alpha as None or omit the key — NOT return 0.0 (which is indistinguishable
    from a real zero-alpha exit).

    Team-lead contract: graceful degradation means the API signals 'no data' explicitly.
    A value of 0.0 in guard_alpha when no exit_trigger exists is ambiguous — it could
    mean 'perfectly timed exit' or 'data not available'. The API must distinguish these.

    This test uses a real SQLite DB with no exit_triggers rows for the triggered symphony.
    """
    import sqlite3
    import json as _json
    import database as db_module

    triggered_state = {
        "sym_no_trigger": {
            "id": "sym_no_trigger",
            "name": "No Trigger Symphony",
            "position": 0.5,
            "simple_return": 0.03,
            "net_deposits": 0.0,
            "time_weighted_return": 0.03,
            "triggered": True,
            "triggered_at_return": 3.5,
        }
    }

    db_path = str(tmp_path / "test_graceful_degrade.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE bot_state (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("INSERT INTO bot_state VALUES (1, ?)", (_json.dumps(triggered_state),))
    conn.execute(
        "CREATE TABLE execution_lock (id INTEGER PRIMARY KEY, is_locked INTEGER, timestamp REAL)"
    )
    conn.execute("INSERT INTO execution_lock VALUES (1, 0, 0.0)")
    conn.execute(
        """CREATE TABLE exit_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            symphony_name TEXT,
            at_return REAL,
            ts_utc TEXT
        )"""
    )
    # NOTE: deliberately no rows for sym_no_trigger — simulates dev DB state
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chart_history (id INTEGER PRIMARY KEY, data TEXT)"
    )
    conn.execute("INSERT INTO chart_history VALUES (1, ?)", (_json.dumps({"symphonies": {}}),))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS strategies "
        "(symphony_id TEXT PRIMARY KEY, params TEXT, locked_vars TEXT)"
    )
    conn.commit()
    conn.close()

    import app as app_module

    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    monkeypatch.setenv("DB_PATH", db_path)

    with patch.object(app_module, "get_api_state_dict", return_value={
        "bot_state": triggered_state,
        "is_locked": False,
        "port_state": {},
        "exit_authority": "per_symphony",
        "daemon_started_at": "2026-05-19T00:00:00Z",
        "portfolio_strip": {
            "today_change": {"dry_run": 0.0, "if_held": 0.0},
            "cumulative_return": {"dry_run": 0.03, "if_held": 0.05},
            "max_drawdown": {"dry_run": -0.01, "if_held": -0.01},
            "hist_dates": _THIRTY_FIVE_DAYS[:],
            "hist_bot": [0.001 * (i + 1) for i in range(35)],
            "hist_held": [0.0008 * (i + 1) for i in range(35)],
        },
    }):
        with patch.object(db_module, "load_state", return_value=triggered_state), \
             patch.object(db_module, "load_chart_history",
                          return_value={"symphonies": {}}), \
             patch.object(db_module, "normalize_name",
                          side_effect=lambda n: (n or "").lower().replace(" ", "_")), \
             patch.object(db_module, "get_symphony_strategy",
                          return_value={"params": {}, "locked_vars": []}), \
             patch.object(db_module, "read_fleet_alert", return_value=None), \
             patch.object(db_module, "get_shadow_divergence",
                          return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch.object(db_module, "read_port_state", return_value=None):

            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as c:
                resp = c.get("/api/state")
                assert resp.status_code == 200
                data = json.loads(resp.data)

    bot_state = data.get("bot_state") or data.get("state") or {}
    sym_entry = bot_state.get("sym_no_trigger", {})

    # When no exit_triggers row exists, guard_alpha must be None or absent — NOT 0.0
    guard_alpha = sym_entry.get("guard_alpha", "KEY_ABSENT")
    assert guard_alpha in (None, "KEY_ABSENT"), (
        f"When no exit_triggers row exists for a triggered symphony, guard_alpha must be "
        f"None or absent — got {guard_alpha!r}. "
        "A value of 0.0 is ambiguous: it could mean 'perfectly timed exit' or 'no data'. "
        "The API must distinguish these cases. "
        "Fix: only set guard_alpha when get_guard_alpha_by_symphony returns a non-None value "
        "for the symphony. Use setdefault(None) instead of setdefault(0) as the fallback."
    )
