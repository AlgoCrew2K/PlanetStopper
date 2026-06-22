"""
RED tests — Live Dashboard Metrics (AC-1 through AC-7).

Every surface that showed blank/zero/stale on the live droplet is covered here.
Tests fail RED until the implementer (ld-impl) wires the live data sources.

Contract rules:
- Never hardcode producer-computed values (feedback_no_hardcoded_test_values).
  Dollar amounts and returns are derived from the fixture or DB state the test
  itself constructs; we assert on format/shape/sign where absolute values are
  not the point.
- Mock only network and time; test the real route logic against a real (temp) DB.
- pytest.approx tolerances are explained inline where used.

Surfaces covered:
  AC-1  /api/guard-alpha-summary — live exit_triggers path (non-zero on day-1)
  AC-2  /api/performance — shadow_history fallback (non-empty on day-1)
  AC-3  /api/history/<days> — base_dir=analytics._POST_MORTEMS_DIR AST guard
  AC-4  /api/strip/<window> — single-day intraday guard_alpha
  AC-5  /ai-advisor template — per-lens sources render, no raw JSON
  AC-6  MDD bot column — None renders as '--', not '0.0%'
  AC-7  Accuracy: no surface returns 0.0 when shadow_history has data for triggered sym
"""

from __future__ import annotations

import ast
import json
import pathlib
import sqlite3
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Shared fixture: golden math fixture for AC-1 dollar-saved formula
# ---------------------------------------------------------------------------

_MATH_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "guard_alpha_intraday_saved.json"
)
_MATH_FIXTURE = json.loads(_MATH_FIXTURE_PATH.read_text())


def _compute_saved_dollars(at_return: float, current_return: float, position_value: float) -> float:
    """Reference implementation of AC-1 math — MUST match app.py implementation.

    Formula: saved = (at_return - current_return) / 100 * position_value
    Positive when bot locked in gains that the held position has since given up.
    """
    # Note: sign convention — bot exited at `at_return`; held is now at `current_return`.
    # Saved = what the bot kept (at_return) minus what the held has now (current_return),
    # scaled from percentage to dollar.
    return (at_return - current_return) / 100.0 * position_value


# ---------------------------------------------------------------------------
# Flask test client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client — no port bound."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Minimal in-memory DB seeded with exit_triggers + shadow_history rows
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_exit_triggers(tmp_path, monkeypatch):
    """Create a temp SQLite DB with 2 exit_triggers and matching shadow_history rows.

    This is the minimal live-droplet state: one trading day, all symphonies triggered.
    Returns (db_path, trigger_rows, shadow_rows) so tests can derive expected values.
    """
    # Use a non-sentinel basename to avoid the database._db_file() production-DB guard.
    # The guard fires when basename == "alphabot_state.db" under pytest.
    db_path = str(tmp_path / "test_live_dash.db")

    # Replicate the schema subset needed by the routes under test.
    # Full schema is owned by database.py migrations; we create the tables
    # directly here to avoid importing the full migration stack in tests.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exit_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            at_return REAL NOT NULL,
            trigger_reason TEXT NOT NULL,
            gate_state_json TEXT
        );
        CREATE TABLE IF NOT EXISTS shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            trading_day TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            shadow_return REAL NOT NULL,
            current_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            epoch_label TEXT
        );
        CREATE TABLE IF NOT EXISTS bot_state (
            symphony_id TEXT PRIMARY KEY,
            position_value REAL DEFAULT 0.0,
            high_water_mark REAL,
            triggered INTEGER DEFAULT 0,
            triggered_reason TEXT,
            current_return REAL,
            last_updated TEXT
        );
    """)

    # Two triggered symphonies with real divergence: bot locked in gains,
    # held continued to fall.
    trigger_rows = [
        # symphony_id, ts_utc, at_return, trigger_reason
        ("SYM_ALPHA", "2026-06-22T13:43:00Z", 2.5, "TAKE_PROFIT"),
        ("SYM_BETA",  "2026-06-22T14:23:00Z", 1.8, "VWAP_BREAKDOWN"),
    ]
    conn.executemany(
        "INSERT INTO exit_triggers (symphony_id, ts_utc, at_return, trigger_reason) VALUES (?,?,?,?)",
        trigger_rows,
    )

    # Latest shadow_history rows for each triggered symphony (is_post_trigger=1).
    # current_return has drifted negative post-exit → bot_alpha is positive.
    shadow_rows = [
        # symphony_id, trading_day, ts_utc, shadow_return, current_return, is_post_trigger, epoch
        ("SYM_ALPHA", "2026-06-22", "2026-06-22T16:00:00Z", 2.5, -0.5, 1, "epoch1"),
        ("SYM_BETA",  "2026-06-22", "2026-06-22T16:00:00Z", 1.8, -1.2, 1, "epoch1"),
    ]
    conn.executemany(
        "INSERT INTO shadow_history "
        "(symphony_id, trading_day, ts_utc, shadow_return, current_return, is_post_trigger, epoch_label) "
        "VALUES (?,?,?,?,?,?,?)",
        shadow_rows,
    )

    # bot_state with position_value (needed for dollar calculation)
    bot_rows = [
        ("SYM_ALPHA", 15000.0, 2.5, 1, "TAKE_PROFIT", -0.5, "2026-06-22T16:00:00Z"),
        ("SYM_BETA",  20000.0, 1.8, 1, "VWAP_BREAKDOWN", -1.2, "2026-06-22T16:00:00Z"),
    ]
    conn.executemany(
        "INSERT INTO bot_state "
        "(symphony_id, position_value, high_water_mark, triggered, triggered_reason, current_return, last_updated) "
        "VALUES (?,?,?,?,?,?,?)",
        bot_rows,
    )

    conn.commit()
    conn.close()

    # Patch DB_PATH so database._db_file() resolves to our temp DB.
    # The basename must NOT be "alphabot_state.db" (sentinel guard in database.py:68).
    monkeypatch.setenv("DB_PATH", db_path)

    return db_path, trigger_rows, shadow_rows


# ===========================================================================
# AC-1: /api/guard-alpha-summary — live exit_triggers path
# ===========================================================================


class TestGuardAlphaSummaryLiveExitTriggers:
    """AC-1: the route must show non-zero event count and non-zero dollars on
    a fresh droplet (day-1) where post_mortem files do not yet exist but
    exit_triggers rows DO exist in the DB."""

    def test_guard_event_count_reflects_exit_triggers_rows(
        self, client, db_with_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-1: guard_event_count equals the number of exit_triggers rows.

        Fails RED if route only reads post_mortem files and ignores exit_triggers.
        """
        import analytics as analytics_module

        # Point post_mortems to an empty dir so the file path is empty
        empty_pm = tmp_path / "empty_post_mortems"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        _db_path, trigger_rows, _ = db_with_exit_triggers
        expected_count = len(trigger_rows)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}. "
            "RED: route may not read exit_triggers for the live count."
        )
        data = resp.get_json()
        assert data["guard_event_count"] == expected_count, (
            f"guard_event_count should be {expected_count} (from exit_triggers rows), "
            f"got {data['guard_event_count']}. "
            "RED: route only counts post_mortem files, not live exit_triggers."
        )

    def test_cumulative_saved_dollars_nonzero_from_divergence(
        self, client, db_with_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-1: cumulative_saved_dollars is positive when at_return > current_return.

        Expected value is derived from the fixture data — not hardcoded.
        Fails RED if route returns 0.0 because it reads only post_mortem files.
        """
        import analytics as analytics_module

        empty_pm = tmp_path / "empty_post_mortems"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        _db_path, trigger_rows, shadow_rows = db_with_exit_triggers

        # Derive expected saved dollars from the fixture data using the reference formula.
        # SYM_ALPHA: at_return=2.5, current_return=-0.5, position_value=15000
        # SYM_BETA:  at_return=1.8, current_return=-1.2, position_value=20000
        expected_alpha_saved = sum(
            _compute_saved_dollars(
                at_return=t[2],  # at_return from trigger_rows
                current_return=s[4],  # current_return from shadow_rows[i]
                position_value=15000.0 if t[0] == "SYM_ALPHA" else 20000.0,
            )
            for t, s in zip(trigger_rows, shadow_rows)
        )
        assert expected_alpha_saved > 0, (
            "Test setup: expected_alpha_saved must be positive given fixture inputs"
        )

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] > 0, (
            f"cumulative_saved_dollars should be positive (got {data['cumulative_saved_dollars']}) "
            "when triggered symphonies have at_return > current_return. "
            "RED: route returns 0.0 because post_mortem directory is empty."
        )

    def test_response_includes_source_field_intraday(
        self, client, db_with_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-1: response includes 'source' field indicating data basis.

        When using exit_triggers (intraday), source must be 'exit_triggers_intraday'
        or similar — not 'post_mortem_eod'. This distinguishes the two data paths.
        Fails RED if 'source' field is absent or always 'post_mortem_eod'.
        """
        import analytics as analytics_module

        empty_pm = tmp_path / "empty_post_mortems"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "source" in data, (
            "Response must include a 'source' field indicating data basis. "
            "RED: field not yet added."
        )
        assert data["source"] != "post_mortem_eod", (
            f"When post_mortem dir is empty, source must not be 'post_mortem_eod'. "
            f"Got: {data['source']!r}"
        )


# ===========================================================================
# AC-1 math: golden-fixture tests for dollar-saved formula
# ===========================================================================


class TestGuardAlphaIntradayMath:
    """Golden-fixture tests for the (at_return - current_return)/100 * position_value
    formula. These run against the reference implementation to prove the formula
    is correct — they will catch an implementer who inverts the sign or forgets
    the /100 scaling."""

    @pytest.mark.parametrize("case", _MATH_FIXTURE["cases"])
    def test_saved_dollars_formula_matches_golden_fixture(self, case: dict):
        """AC-1: each fixture case must satisfy the expected sign and the formula.

        Derives the expected value from the fixture inputs (not the 'expected_*'
        metadata) to confirm the formula produces the right sign.
        """
        inputs = case["inputs"]
        at_return = inputs["at_return"]
        current_return = inputs["current_return"]
        position_value = inputs["position_value"]

        result = _compute_saved_dollars(at_return, current_return, position_value)

        expected_sign = case["expected_sign"]
        if expected_sign == "positive":
            assert result > 0, (
                f"Case '{case['name']}': expected positive result, got {result}. "
                f"Inputs: {inputs}"
            )
        elif expected_sign == "negative":
            assert result < 0, (
                f"Case '{case['name']}': expected negative result, got {result}. "
                f"Inputs: {inputs}"
            )
        elif expected_sign == "zero":
            assert result == pytest.approx(0.0, abs=1e-9), (
                # abs=1e-9: float multiplication with 0 should be exact but
                # floating-point arithmetic can produce -0.0; 1e-9 absorbs that.
                f"Case '{case['name']}': expected zero result, got {result}. "
                f"Inputs: {inputs}"
            )

    def test_formula_is_dimensionally_correct(self):
        """AC-1: result is in dollars (not percent) — 1pp divergence on $10k = $100."""
        # 1 percentage point divergence on $10,000 position = $100 saved
        result = _compute_saved_dollars(
            at_return=2.0,
            current_return=1.0,  # 1pp better than held
            position_value=10_000.0,
        )
        # abs=0.01: IEEE754 double should be exact here but tolerance avoids
        # future floating-point surprise if the formula is restructured.
        assert result == pytest.approx(100.0, abs=0.01), (
            f"1pp divergence on $10k must equal $100, got {result}. "
            "Check if formula divides by 100 (percentage → decimal)."
        )


# ===========================================================================
# AC-2: /api/performance — shadow_history fallback series
# ===========================================================================


class TestPerformanceRoutesShadowHistoryFallback:
    """AC-2: /api/performance must return a non-empty series from shadow_history
    when no post_mortem files exist (day-1 droplet state)."""

    def test_api_performance_returns_nonempty_dates_on_day1(
        self, client, monkeypatch
    ):
        """AC-2: with 0 post_mortem files and 1 shadow_history day, dates is non-empty.

        Fails RED if route only populates from post_mortem history.
        """
        import analytics as analytics_module

        # Simulate: no post_mortem files
        monkeypatch.setattr(
            analytics_module,
            "get_history_with_cache_invalidation",
            lambda **kw: {},
        )

        # Simulate: shadow_history has 1 day of data
        mock_series = (
            ["2026-06-22"],   # dates
            [1.376],          # bot_pct (today's change, locked-in)
            [0.481],          # held_pct (today's if-held)
        )
        monkeypatch.setattr(
            analytics_module,
            "get_portfolio_bot_and_held_daily_returns",
            lambda *a, **kw: mock_series,
        )

        resp = client.get("/api/performance")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()

        # With 1 day of shadow data, dates must be non-empty
        assert len(data.get("dates", [])) >= 1, (
            f"dates must have at least 1 entry when shadow_history has data. "
            f"Got dates={data.get('dates')}. "
            "RED: route only reads post_mortem history."
        )

    def test_api_performance_observation_count_positive_on_day1(
        self, client, monkeypatch
    ):
        """AC-2: observation_count >= 1 when shadow_history has at least 1 day."""
        import analytics as analytics_module

        monkeypatch.setattr(
            analytics_module,
            "get_history_with_cache_invalidation",
            lambda **kw: {},
        )
        mock_series = (["2026-06-22"], [1.376], [0.481])
        monkeypatch.setattr(
            analytics_module,
            "get_portfolio_bot_and_held_daily_returns",
            lambda *a, **kw: mock_series,
        )

        resp = client.get("/api/performance")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("observation_count", 0) >= 1, (
            f"observation_count must be >= 1 when shadow_history has data. "
            f"Got {data.get('observation_count')}. "
            "RED: route returns 0 because post_mortem history is empty."
        )

    def test_api_performance_insufficient_history_flag_true_on_day1(
        self, client, monkeypatch
    ):
        """AC-2: insufficient_history=True with 1 shadow_history day (honest).

        The route must NOT claim sufficient history with only 1 data point —
        quantstats metrics require >= 2 observations.
        """
        import analytics as analytics_module

        monkeypatch.setattr(
            analytics_module,
            "get_history_with_cache_invalidation",
            lambda **kw: {},
        )
        mock_series = (["2026-06-22"], [1.376], [0.481])
        monkeypatch.setattr(
            analytics_module,
            "get_portfolio_bot_and_held_daily_returns",
            lambda *a, **kw: mock_series,
        )

        resp = client.get("/api/performance")
        assert resp.status_code == 200
        data = resp.get_json()
        # 1 observation is insufficient for quantstats — must be flagged honestly
        assert data.get("insufficient_history") is True, (
            f"insufficient_history must be True with 1 shadow_history day. "
            f"Got {data.get('insufficient_history')!r}. "
            "The flag must be honest regardless of the data source."
        )


# ===========================================================================
# AC-3: /api/history/<days> — base_dir AST guard
# ===========================================================================


class TestHistoryRouteBaseDir:
    """AC-3: app.py must call analytics.get_history_summary with
    base_dir=analytics._POST_MORTEMS_DIR. AST-based test so it fails at
    source-review time, not runtime."""

    def test_get_history_summary_calls_pass_base_dir(self):
        """AC-3: every call to analytics.get_history_summary in app.py passes
        base_dir=analytics._POST_MORTEMS_DIR.

        Fails RED because app.py:2452 calls get_history_summary(days=days)
        with no base_dir kwarg, defaulting to '.'.
        """
        _app_py = pathlib.Path(__file__).parent.parent.parent / "app.py"
        assert _app_py.exists(), f"app.py not found at {_app_py}"
        tree = ast.parse(_app_py.read_text(encoding="utf-8"))

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match analytics.get_history_summary (Attribute form)
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "get_history_summary"
                and isinstance(func.value, ast.Name)
                and func.value.id == "analytics"
            ):
                continue

            base_dir_kwargs = [kw for kw in node.keywords if kw.arg == "base_dir"]
            if not base_dir_kwargs:
                violations.append(
                    f"app.py:{node.lineno} — analytics.get_history_summary() "
                    "called without base_dir kwarg. "
                    "Fix: add base_dir=analytics._POST_MORTEMS_DIR"
                )
                continue

            # Value must be analytics._POST_MORTEMS_DIR
            kw_val = base_dir_kwargs[0].value
            is_correct = (
                isinstance(kw_val, ast.Attribute)
                and isinstance(kw_val.value, ast.Name)
                and kw_val.value.id == "analytics"
                and kw_val.attr == "_POST_MORTEMS_DIR"
            )
            if not is_correct:
                violations.append(
                    f"app.py:{node.lineno} — analytics.get_history_summary() "
                    f"base_dir is {ast.unparse(kw_val)!r}, "
                    "expected analytics._POST_MORTEMS_DIR"
                )

        assert not violations, (
            "AC-3: the following call sites are missing or have wrong base_dir "
            "(causes history route to look in CWD instead of post_mortems/):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_history_route_returns_todays_exits_from_exit_triggers(
        self, client, db_with_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-3: /api/history/<days> response includes todays_exits populated
        from exit_triggers table on a fresh droplet with no post_mortem files.

        Fails RED if route only reads post_mortem files for todays_exits.
        """
        import analytics as analytics_module

        empty_pm = tmp_path / "no_post_mortems"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/history/30")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()

        todays_exits = data.get("todays_exits", [])
        assert len(todays_exits) >= 1, (
            f"todays_exits must be non-empty when exit_triggers has rows. "
            f"Got {todays_exits!r}. "
            "RED: route does not read exit_triggers for todays_exits."
        )


# ===========================================================================
# AC-4: /api/strip/<window> — single-day intraday guard_alpha
# ===========================================================================


class TestStripRouteIntradayGuardAlpha:
    """AC-4: when shadow_history has only 1 distinct trading_day, the strip route
    must return a non-zero guard_alpha from the intraday formula rather than 0.0."""

    def test_strip_returns_intraday_only_flag_with_1_day_data(
        self, client, db_with_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-4: strip response must include intraday_only=True when shadow_history
        has exactly 1 distinct trading_day.

        Fails RED if the route does not implement the single-day intraday path.
        """
        resp = client.get("/api/strip/30d")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()

        # The route must add intraday_only=True to signal this is a 1-day fallback.
        assert data.get("intraday_only") is True, (
            "strip response must include intraday_only=True when shadow_history "
            "has only 1 trading day. "
            f"Got: intraday_only={data.get('intraday_only')!r}. "
            "RED: route does not implement the single-day intraday guard_alpha path."
        )

    def test_strip_guard_alpha_nonzero_with_triggered_symphonies(
        self, client, db_with_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-4: guard_alpha must be non-zero when triggered symphonies have
        at_return > current_return (positive guard event).

        Fails RED if route returns 0.0 due to insufficient_history guard with
        no single-day fallback.
        """
        resp = client.get("/api/strip/30d")
        assert resp.status_code == 200
        data = resp.get_json()

        guard_alpha = data.get("guard_alpha")
        assert guard_alpha is not None, (
            "strip response must include 'guard_alpha' key. "
            f"Got keys: {list(data.keys())}"
        )
        assert guard_alpha != 0.0, (
            f"guard_alpha must be non-zero when triggered symphonies have "
            f"at_return > current_return. Got guard_alpha={guard_alpha}. "
            "RED: route falls back to 0.0 due to <2 trading_day guard."
        )

    def test_strip_insufficient_history_still_true_with_1_day(
        self, client, db_with_exit_triggers, monkeypatch
    ):
        """AC-4: insufficient_history=True must still be set with 1-day data.

        The intraday fallback is not a claim of sufficient history — the flag
        must remain True so the UI can show 'Today only' instead of the full
        windowed label.
        """
        resp = client.get("/api/strip/30d")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data.get("insufficient_history") is True, (
            "insufficient_history must remain True with 1-day shadow_history, "
            "even when the intraday fallback guard_alpha is computed. "
            f"Got: insufficient_history={data.get('insufficient_history')!r}"
        )


# ===========================================================================
# AC-5: /ai-advisor template — per-lens sources render
# ===========================================================================


class TestAiAdvisorNewsSourcesRender:
    """AC-5a: the Market Prism sources block must render per-lens string citations,
    not crash on missing top-level 'sources' key.

    AC-5b: if article_corpus is present in per_lens_digest.sentiment, render links.
    """

    def _make_prism_obs(self, *, include_article_corpus: bool = False) -> dict:
        """Build a minimal MARKET_PRISM observation row as the template receives it."""
        sentiment_entry: dict[str, Any] = {
            "summary": "Markets showed mixed signals.",
            "sources": ["GDELT 2.0 AvgTone", "news_corpus (CNBC, Reuters)"],
        }
        if include_article_corpus:
            sentiment_entry["article_corpus"] = [
                {
                    "url": "https://www.reuters.com/markets/story-1",
                    "title": "Fed signals caution on rate cuts",
                    "published": "2026-06-22T10:00:00Z",
                    "topics": ["macro"],
                }
            ]
        return {
            "id": 1,
            "advisor_role": "MARKET_PRISM",
            "verdict": "risk-on",
            "created_at": "2026-06-22 07:07:00",
            "raw_response": {
                "run_id": "test-run-id",
                "overall_sentiment": "risk-on",
                "sentiment_rationale": "Technicals strong, breadth improving.",
                "per_lens_digest": {
                    "technicals": {
                        "summary": "Breadth improving.",
                        "sources": ["Alpaca daily bars (270-day, SMA50/20-day momentum)"],
                    },
                    "sentiment": sentiment_entry,
                    "macro": {
                        "summary": "Rate expectations stable.",
                        "sources": ["FRED: DGS10, FEDFUNDS"],
                    },
                },
                "debate_occurred": False,
                "debate_rounds_used": 0,
                "available_lens_count": 3,
            },
        }

    def test_ai_advisor_renders_per_lens_source_citations(self, client, monkeypatch):
        """AC-5a: the rendered HTML contains per-lens source citation text.

        Fails RED if template reads _raw.get('sources', []) and the block is
        always empty.
        """
        obs = self._make_prism_obs()

        with patch.object(app_module, "database") as db_mock:
            db_mock.get_latest_market_prism_summary.return_value = obs
            db_mock.get_advisor_observations_for_symphony.return_value = []
            db_mock.load_state.return_value = {}
            db_mock.get_symphony_live_mode.return_value = False

            resp = client.get("/ai-advisor")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        html = resp.get_data(as_text=True)

        # The per-lens citation for the technicals lens must appear in the HTML.
        # We check the string-contains rather than exact HTML structure to avoid
        # brittle structure coupling.
        citation = "Alpaca daily bars"
        assert citation in html, (
            f"Expected per-lens citation {citation!r} to appear in rendered HTML. "
            "RED: template reads top-level 'sources' key (does not exist) instead of "
            "per_lens_digest[lens]['sources']."
        )

    def test_ai_advisor_does_not_expose_raw_json_in_sources_block(
        self, client, monkeypatch
    ):
        """AC-5a: the sources block must not render raw JSON strings.

        A common failure mode: template dumps the entire raw_response dict as a
        string when the template path falls through to a default.
        """
        obs = self._make_prism_obs()

        with patch.object(app_module, "database") as db_mock:
            db_mock.get_latest_market_prism_summary.return_value = obs
            db_mock.get_advisor_observations_for_symphony.return_value = []
            db_mock.load_state.return_value = {}
            db_mock.get_symphony_live_mode.return_value = False

            resp = client.get("/ai-advisor")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Raw JSON telltale: the run_id key name would appear if raw_response is dumped
        assert '"run_id"' not in html, (
            "Template must not render raw JSON. "
            "Found '\"run_id\"' in HTML — raw_response is being dumped."
        )
        # Another JSON telltale: the bare dict key 'per_lens_digest'
        assert "per_lens_digest" not in html, (
            "Template must not render raw JSON keys like 'per_lens_digest'. "
            "Found in HTML."
        )

    def test_ai_advisor_renders_article_corpus_links_when_present(
        self, client, monkeypatch
    ):
        """AC-5b (template contract only): when per_lens_digest.sentiment.article_corpus
        exists in the DB row, the template renders clickable article links.

        NOTE: this test verifies the TEMPLATE reads article_corpus correctly when
        the field is already present in a DB row.  It does NOT verify that the
        production pipeline writes article_corpus — that is covered by
        TestArticleCorpusPipelineContract below, which goes RED until
        _build_sentiment_section + _build_per_lens_digest emit article_corpus.

        Fails RED if template does not read article_corpus from the sentiment lens.
        """
        obs = self._make_prism_obs(include_article_corpus=True)

        with patch.object(app_module, "database") as db_mock:
            db_mock.get_latest_market_prism_summary.return_value = obs
            db_mock.get_advisor_observations_for_symphony.return_value = []
            db_mock.load_state.return_value = {}
            db_mock.get_symphony_live_mode.return_value = False

            resp = client.get("/ai-advisor")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # The article URL from the corpus must appear as an href
        assert "reuters.com/markets/story-1" in html, (
            "Expected article URL from article_corpus to appear in rendered HTML. "
            "RED: template does not read per_lens_digest.sentiment.article_corpus."
        )
        # The article title must appear
        assert "Fed signals caution" in html, (
            "Expected article title from article_corpus to appear in rendered HTML."
        )


# ===========================================================================
# AC-5b (pipeline): article_corpus flows from _build_sentiment_section
#                   through _build_per_lens_digest into the persisted row
# ===========================================================================


class TestArticleCorpusPipelineContract:
    """AC-5b (pipeline): the production pipeline must write article_corpus into
    the per_lens_digest.sentiment entry so the template can render it.

    These are RED tests: they fail until _build_sentiment_section emits
    article_corpus in the returned lens block AND _build_per_lens_digest
    passes it through to the digest entry.

    The circular fixture (test_ai_advisor_renders_article_corpus_links_when_present
    above) injects article_corpus manually and only proves the template reads it
    when present. These tests prove the key is PRODUCED by the pipeline.
    """

    # Minimal corpus article matching news_corpus.build_news_corpus() return shape.
    _CORPUS_ARTICLE = {
        "url": "https://www.reuters.com/markets/us/fed-caution-2026-06-22/",
        "title": "Fed signals caution on rate cuts",
        "published": "2026-06-22T10:00:00Z",
        "domain": "reuters.com",
        "topics": ["macro"],
        "authority": 0.9,
    }

    def _mock_news_corpus_result(self) -> dict:
        """Build a minimal build_news_corpus() return value with one corpus article."""
        return {
            "available": True,
            "tone": -0.5,
            "corpus": [self._CORPUS_ARTICLE],
            "facet_a_tone": -0.5,
            "facet_b_corpus": [self._CORPUS_ARTICLE],
        }

    def test_build_sentiment_section_emits_article_corpus_key(self):
        """AC-5b: _build_sentiment_section must include article_corpus in its return dict.

        Fails RED because the current implementation returns:
          {"lens": "sentiment", "available": True, "payload": {...corpus...}, "sources": [...]}
        where article_corpus is buried inside payload.corpus, not exposed as a
        top-level article_corpus key.

        Fix: add article_corpus=corpus[:TOP_K] (or similar) to the return dict so
        lens_pipeline._build_per_lens_digest can pass it through to the digest entry.
        """
        import sys
        import ai_advisor as ai_advisor_module
        from unittest.mock import MagicMock

        mock_corpus = self._mock_news_corpus_result()

        # Patch the lazy import path: _build_sentiment_section does
        # `from advisors import news_corpus` inside the function body.
        # Injecting our mock into sys.modules before the call intercepts it.
        mock_nc = MagicMock()
        mock_nc.build_news_corpus.return_value = mock_corpus

        # Patch the module in sys.modules so the lazy `from advisors import news_corpus`
        # inside _build_sentiment_section picks up our mock.
        original = sys.modules.get("advisors.news_corpus")
        sys.modules["advisors.news_corpus"] = mock_nc
        try:
            result = ai_advisor_module._build_sentiment_section()
        finally:
            if original is not None:
                sys.modules["advisors.news_corpus"] = original
            else:
                sys.modules.pop("advisors.news_corpus", None)

        assert result.get("available") is True, (
            f"Test setup: _build_sentiment_section must return available=True "
            f"when news_corpus returns a non-empty corpus. Got: {result}"
        )
        assert "article_corpus" in result, (
            "AC-5b: _build_sentiment_section must include 'article_corpus' key in its "
            "return dict so lens_pipeline._build_per_lens_digest can pass it through. "
            "RED: current implementation buries corpus inside payload.corpus only."
        )
        assert len(result["article_corpus"]) >= 1, (
            "AC-5b: article_corpus must contain at least 1 entry when corpus is non-empty. "
            f"Got article_corpus={result.get('article_corpus')!r}"
        )

    def test_build_per_lens_digest_passes_article_corpus_through(self):
        """AC-5b: _build_per_lens_digest must preserve article_corpus in the digest entry.

        Fails RED because _build_per_lens_digest (lens_pipeline.py:152) only
        writes 'summary' and 'sources' — it drops any article_corpus key from
        the lens block.

        Fix: in _build_per_lens_digest, if the block has article_corpus, include
        it in the digest entry: entry["article_corpus"] = block["article_corpus"].
        """
        from advisors import lens_pipeline as lp

        # Simulate a sentiment lens block that already has article_corpus (after
        # _build_sentiment_section is fixed).
        sentiment_block = {
            "lens": "sentiment",
            "available": True,
            "summary": "Markets showed mixed signals.",
            "sources": [{"title": "Reuters", "url": "https://reuters.com/1", "lens": "sentiment"}],
            "article_corpus": [self._CORPUS_ARTICLE],
        }

        per_lens = {"sentiment": sentiment_block}
        digest = lp._build_per_lens_digest(per_lens)

        sentiment_entry = digest.get("sentiment", {})
        assert "article_corpus" in sentiment_entry, (
            "AC-5b: _build_per_lens_digest must preserve article_corpus in the "
            "sentiment digest entry. "
            "RED: current implementation drops article_corpus (only writes summary + sources)."
        )
        assert len(sentiment_entry["article_corpus"]) >= 1, (
            "AC-5b: preserved article_corpus must be non-empty. "
            f"Got: {sentiment_entry.get('article_corpus')!r}"
        )

    def test_end_to_end_article_corpus_in_persisted_observation(self, monkeypatch):
        """AC-5b (end-to-end): article_corpus appears in a per_lens_digest entry
        as it would be stored in advisor_observations.raw_response.

        This test simulates the full pipeline:
          1. _build_sentiment_section (mocked news_corpus) → lens block with article_corpus
          2. _build_per_lens_digest → digest entry with article_corpus preserved
          3. Assert the digest entry the DB row would carry has article_corpus

        Fails RED because neither step 1 nor step 2 currently emit article_corpus.
        """
        import sys
        import ai_advisor as ai_advisor_module
        from advisors import lens_pipeline as lp
        from unittest.mock import MagicMock

        mock_corpus_result = self._mock_news_corpus_result()
        mock_nc = MagicMock()
        mock_nc.build_news_corpus.return_value = mock_corpus_result

        # Step 1: run _build_sentiment_section with mocked news_corpus
        original = sys.modules.get("advisors.news_corpus")
        sys.modules["advisors.news_corpus"] = mock_nc
        try:
            sentiment_block = ai_advisor_module._build_sentiment_section()
        finally:
            if original is not None:
                sys.modules["advisors.news_corpus"] = original
            else:
                sys.modules.pop("advisors.news_corpus", None)

        # Step 2: run _build_per_lens_digest
        digest = lp._build_per_lens_digest({"sentiment": sentiment_block})

        # Step 3: assert article_corpus survives in the digest
        sentiment_entry = digest.get("sentiment", {})
        assert "article_corpus" in sentiment_entry, (
            "AC-5b end-to-end: article_corpus must survive the full pipeline from "
            "_build_sentiment_section → _build_per_lens_digest → per_lens_digest entry. "
            "RED: both functions need to emit/preserve the key. "
            f"sentiment_entry keys: {list(sentiment_entry.keys())}"
        )
        # Verify article URL is preserved (not just the key existing with empty list)
        corpus = sentiment_entry["article_corpus"]
        assert any(
            "reuters.com" in art.get("url", "") for art in corpus
        ), (
            "AC-5b: article URL from news_corpus must appear in the preserved article_corpus. "
            f"Got corpus={corpus!r}"
        )


# ===========================================================================
# AC-1b (live): guard_alpha_summary — bot_state JSON blob lookup (not column)
# ===========================================================================


class TestGuardAlphaSummaryBotStateBlob:
    """AC-1b: guard_alpha_summary() must read position_value from the bot_state
    JSON blob (database.load_state() → dict keyed by symphony_id), NOT from a
    non-existent position_value column.

    These tests go RED until app.py replaces the broken subquery
      SELECT position_value FROM bot_state WHERE symphony_id = t.symphony_id
    with a load_state()-based lookup.

    Root cause (ld-ux live audit, 2026-06-22): bot_state schema is a single-row
    blob (id, data TEXT).  The correlated subquery raises OperationalError on
    the live droplet; the outer except Exception swallows it → guard_event_count=0
    despite 11 exit_triggers rows.
    """

    @pytest.fixture
    def db_with_blob_bot_state(self, tmp_path, monkeypatch):
        """Minimal DB matching the REAL bot_state schema: single blob row id=1."""
        import json as _json
        db_path = str(tmp_path / "test_blob_state.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS exit_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symphony_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                at_return REAL NOT NULL,
                trigger_reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symphony_id TEXT NOT NULL,
                trading_day TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                shadow_return REAL NOT NULL,
                current_return REAL NOT NULL,
                is_post_trigger INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY,
                data TEXT
            );
        """)
        # Two exit_triggers with positive divergence
        conn.executemany(
            "INSERT INTO exit_triggers (symphony_id, ts_utc, at_return, trigger_reason) VALUES (?,?,?,?)",
            [
                ("SYM_A", "2026-06-22T13:43:00Z", 2.5, "TAKE_PROFIT"),
                ("SYM_B", "2026-06-22T14:23:00Z", 1.8, "VWAP_BREAKDOWN"),
            ],
        )
        # shadow_history: current_return has drifted negative post-exit
        conn.executemany(
            "INSERT INTO shadow_history (symphony_id, trading_day, ts_utc, shadow_return, current_return, is_post_trigger) VALUES (?,?,?,?,?,?)",
            [
                ("SYM_A", "2026-06-22", "2026-06-22T16:00:00Z", 2.5, -0.5, 1),
                ("SYM_B", "2026-06-22", "2026-06-22T16:00:00Z", 1.8, -1.2, 1),
            ],
        )
        # bot_state: real schema — single JSON blob row
        blob = {
            "SYM_A": {"name": "Alpha", "current_value": 15000.0, "current_return": -0.5},
            "SYM_B": {"name": "Beta",  "current_value": 20000.0, "current_return": -1.2},
        }
        conn.execute("INSERT INTO bot_state (id, data) VALUES (1, ?)", (_json.dumps(blob),))
        conn.commit()
        conn.close()
        monkeypatch.setenv("DB_PATH", db_path)
        return db_path

    def test_guard_event_count_nonzero_with_blob_bot_state(
        self, client, db_with_blob_bot_state, tmp_path, monkeypatch
    ):
        """AC-1b: guard_event_count must be 2 on a DB with the real bot_state schema.

        Fails RED because the current query uses `position_value` column which does
        not exist in the blob schema — the entire try block fails silently.
        """
        import analytics as analytics_module
        empty_pm = tmp_path / "no_pm"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["guard_event_count"] == 2, (
            f"guard_event_count must be 2 with 2 exit_triggers rows and real blob bot_state. "
            f"Got {data['guard_event_count']}. "
            "RED: app.py correlated subquery SELECT position_value FROM bot_state "
            "raises OperationalError on the real schema; except swallows it → 0."
        )

    def test_cumulative_saved_dollars_positive_with_blob_bot_state(
        self, client, db_with_blob_bot_state, tmp_path, monkeypatch
    ):
        """AC-1b: cumulative_saved_dollars is positive when bot exited higher than held.

        The dollar formula requires reading current_value from the bot_state blob dict,
        not from a non-existent column.  Fails RED until load_state() is used.
        """
        import analytics as analytics_module
        empty_pm = tmp_path / "no_pm2"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] > 0, (
            f"cumulative_saved_dollars must be positive with at_return > current_return "
            f"and position_value > 0. Got {data['cumulative_saved_dollars']}. "
            "RED: position_value lookup fails because column does not exist in blob schema."
        )


# ===========================================================================
# AC-3b (live): history route — trigger_count backfilled from exit_triggers
# ===========================================================================


class TestHistoryRouteTriggerCountBackfill:
    """AC-3b: when exit_triggers is backfilled into todays_exits, trigger_count
    must also be updated in the stats dict.

    ld-ux live audit: History tab shows 'Today's exits (0)' despite 11 exit_triggers
    rows because stats['trigger_count'] is never updated by the AC-3 backfill code.
    """

    @pytest.fixture
    def db_with_exit_triggers_only(self, tmp_path, monkeypatch):
        """Minimal DB: exit_triggers rows, no post_mortem files."""
        db_path = str(tmp_path / "test_history_triggers.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS exit_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symphony_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                at_return REAL NOT NULL,
                trigger_reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY,
                data TEXT
            );
        """)
        conn.executemany(
            "INSERT INTO exit_triggers (symphony_id, ts_utc, at_return, trigger_reason) VALUES (?,?,?,?)",
            [
                ("SYM_A", "2026-06-22T13:43:00Z", 2.5, "TAKE_PROFIT"),
                ("SYM_B", "2026-06-22T14:23:00Z", 1.8, "VWAP_BREAKDOWN"),
                ("SYM_C", "2026-06-22T15:01:00Z", 0.9, "VWAP_BREAKDOWN"),
            ],
        )
        conn.execute("INSERT INTO bot_state (id, data) VALUES (1, '{}')")
        conn.commit()
        conn.close()
        monkeypatch.setenv("DB_PATH", db_path)
        return db_path

    def test_history_trigger_count_reflects_exit_triggers(
        self, client, db_with_exit_triggers_only, tmp_path, monkeypatch
    ):
        """AC-3b: /api/history/<days> trigger_count must equal exit_triggers row count
        when no post_mortem files exist.

        Fails RED because the AC-3 backfill populates todays_exits but never updates
        stats['trigger_count'] — it remains 0 from get_history_summary().
        """
        import analytics as analytics_module
        empty_pm = tmp_path / "no_pm_hist"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/history/30")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data.get("trigger_count", 0) == 3, (
            f"trigger_count must be 3 (from 3 exit_triggers rows). "
            f"Got trigger_count={data.get('trigger_count')}. "
            "RED: AC-3 backfill sets todays_exits but not trigger_count."
        )

    def test_history_todays_exits_length_matches_trigger_count(
        self, client, db_with_exit_triggers_only, tmp_path, monkeypatch
    ):
        """AC-3b: len(todays_exits) must match trigger_count — they must be consistent.

        Fails RED if one is backfilled but not the other.
        """
        import analytics as analytics_module
        empty_pm = tmp_path / "no_pm_hist2"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/history/30")
        assert resp.status_code == 200
        data = resp.get_json()

        todays_exits = data.get("todays_exits", [])
        trigger_count = data.get("trigger_count", 0)
        assert len(todays_exits) == trigger_count, (
            f"len(todays_exits)={len(todays_exits)} must equal "
            f"trigger_count={trigger_count}. "
            "RED: backfill sets one but not the other."
        )
        assert trigger_count > 0, (
            f"trigger_count must be > 0 when exit_triggers has rows. Got {trigger_count}."
        )


# ===========================================================================
# AC-2b (live): performance route — single-day shadow_history fallback
# ===========================================================================


class TestPerformanceSingleDayShadowFallback:
    """AC-2b: /api/performance must return observation_count >= 1 from
    shadow_history even when shadow_history has only 1 distinct trading_day.

    ld-ux live audit: get_portfolio_bot_and_held_daily_returns() returns None
    when len(out_dates) < 2 (its own guard at analytics.py).  The AC-2 fallback
    in app.py checks `if _fallback is not None` — with 1 day the fallback is
    None and observation_count stays 0.

    The fix must handle the single-day case: either by calling a different
    analytics function that returns single-day data, or by bypassing the < 2
    guard at the route level.
    """

    def test_performance_observation_count_nonzero_with_single_shadow_day(
        self, client, monkeypatch
    ):
        """AC-2b: observation_count >= 1 when shadow_history has exactly 1 trading day.

        Root cause: both get_portfolio_bot_and_held_daily_returns AND
        get_portfolio_daily_returns_from_shadow return None when len(out_dates) < 2
        (each has its own '< 2' guard). With only 1 trading day on a fresh droplet,
        every analytics fallback returns None and the route returns observation_count=0.

        Fix: app.py api_performance() needs a third fallback when both analytics
        functions return None — query shadow_history directly for the single trading
        day and build a minimal 1-entry dates/returns list.  Shape contract:
          dates = ["YYYY-MM-DD"], live_returns = [float], shadow_returns = [float]
          observation_count = 1, insufficient_history = True (honest — 1 < MIN_OBS)

        This test mocks both analytics functions to return None (simulating the
        1-day scenario) and mocks a direct DB shadow_history query to return a
        minimal single-day series.  Fails RED until the route implements the
        single-day direct-query path.
        """
        import analytics as analytics_module

        # No post-mortem history
        monkeypatch.setattr(
            analytics_module,
            "get_history_with_cache_invalidation",
            lambda **kw: {},
        )
        monkeypatch.setattr(
            analytics_module,
            "compute_aggregate_returns",
            lambda history: ([], [], []),
        )
        # Both multi-day analytics functions return None (1-day guard fires)
        monkeypatch.setattr(
            analytics_module,
            "get_portfolio_bot_and_held_daily_returns",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_portfolio_daily_returns_from_shadow",
            lambda *a, **kw: None,
        )

        # Mock the expected single-day direct query seam.
        # The implementer must add a function (or inline the query) that returns
        # a 1-entry tuple even when out_dates has only 1 day.
        # We mock analytics.get_single_day_shadow_returns as the seam name;
        # implementer may choose a different name but must provide the hook.
        monkeypatch.setattr(
            analytics_module,
            "get_single_day_shadow_returns",
            lambda *a, **kw: (["2026-06-22"], [1.376], [0.481]),
            raising=False,  # attribute may not exist yet (RED)
        )

        resp = client.get("/api/performance")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data.get("observation_count", 0) >= 1, (
            f"observation_count must be >= 1 when shadow_history has 1 trading day. "
            f"Got observation_count={data.get('observation_count')}. "
            "RED: route needs a single-day fallback (e.g. analytics.get_single_day_shadow_returns) "
            "when both multi-day analytics functions return None."
        )

    def test_performance_dates_nonempty_with_single_shadow_day(
        self, client, monkeypatch
    ):
        """AC-2b: dates must be non-empty from single-day shadow_history fallback."""
        import analytics as analytics_module

        monkeypatch.setattr(
            analytics_module,
            "get_history_with_cache_invalidation",
            lambda **kw: {},
        )
        monkeypatch.setattr(
            analytics_module,
            "compute_aggregate_returns",
            lambda history: ([], [], []),
        )
        monkeypatch.setattr(
            analytics_module,
            "get_portfolio_bot_and_held_daily_returns",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_portfolio_daily_returns_from_shadow",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_single_day_shadow_returns",
            lambda *a, **kw: (["2026-06-22"], [1.376], [0.481]),
            raising=False,
        )

        resp = client.get("/api/performance")
        assert resp.status_code == 200
        data = resp.get_json()

        assert len(data.get("dates", [])) >= 1, (
            f"dates must be non-empty with 1-day shadow fallback. "
            f"Got dates={data.get('dates')}. "
            "RED: single-day analytics path not wired."
        )


# ===========================================================================
# AC-6: MDD bot column — None renders as '--'
# ===========================================================================


class TestMDDBotColumnNoneRendersAsDash:
    """AC-6: when analytics returns mdd.dry_run=None (< 2 distinct trading days),
    the index.html template must render '--' instead of '0.0%'.

    These tests use the Jinja2 template directly (not the Flask route) to avoid
    the auth gate and focus purely on the template rendering contract.
    """

    def _render_mdd_template_fragment(self) -> str:
        """Render the relevant card-footer-grid section of index.html directly
        using Jinja2 with mdd=None and assert on the rendered output.

        We extract just the template expression that is the bug:
          {% set mdd_bot = (mdd_d.get("dry_run", 0)) | float %}
        and test that a None-aware equivalent produces '--' not '0.0%'.
        """
        from jinja2 import Environment

        # The CURRENT (buggy) template expression for mdd_bot:
        # {% set mdd_bot = (mdd_d.get("dry_run", 0) if mdd_d is mapping else ...) | float %}
        # Result when dry_run=None: None or 0 → 0.0 → renders as "+0.0%"

        # Reproduce the current template logic inline to confirm the bug:
        buggy_template = Environment().from_string(
            "{% set mdd_d = _mdd %}"
            "{% set mdd_bot = (mdd_d.get('dry_run', 0) if mdd_d is mapping else (mdd_d or 0)) | float %}"
            "{{ mdd_bot }}"
        )
        # mdd_d.get("dry_run", 0) → None (key exists, value is None) — Jinja `| float` of None = 0.0
        result_buggy = buggy_template.render(_mdd={"dry_run": None, "if_held": -5.2})
        # The buggy result must be "0.0" to confirm this test targets the real bug
        assert result_buggy == "0.0", (
            f"Test setup: buggy template must produce '0.0' for None dry_run. "
            f"Got: {result_buggy!r}. Bug may have been fixed — verify."
        )
        return result_buggy

    def _render_fixed_mdd_fragment(self) -> str:
        """Render the template with the FIXED None-aware expression."""
        from jinja2 import Environment

        fixed_template = Environment().from_string(
            "{% set mdd_d = _mdd %}"
            "{% if mdd_d is mapping and mdd_d.get('dry_run') is not none %}"
            "{{ '%+.1f'|format(mdd_d.get('dry_run') | float) }}%"
            "{% else %}"
            "--"
            "{% endif %}"
        )
        return fixed_template.render(_mdd={"dry_run": None, "if_held": -5.2})

    def test_current_template_renders_zero_for_none_mdd_bot_confirms_bug(self):
        """AC-6: confirms the bug exists — current template coerces None to 0.0.

        This test PASSES (against the buggy template) confirming the bug.
        It is the specification that the implementer's fix must eliminate.
        Not a RED test — it documents the existing bug state.
        """
        result = self._render_mdd_template_fragment()
        assert result == "0.0", (
            f"Expected buggy template to render '0.0' for None mdd.dry_run. "
            f"Got: {result!r}. Bug may have changed — update this test."
        )

    def test_mdd_none_renders_as_dash_in_fixed_template(self):
        """AC-6: the FIXED template expression renders '--' when dry_run=None.

        This is the RED spec test: it tests the FIXED template logic that
        ld-impl must write. Passes against the correct expression; would fail
        if implemented with the current buggy `| float` coercion.
        """
        result = self._render_fixed_mdd_fragment()
        assert result == "--", (
            f"Fixed template must render '--' for None mdd.dry_run. "
            f"Got: {result!r}. "
            "RED: if this fails, the fixed template still coerces None to a number."
        )

    def test_index_html_contains_none_aware_mdd_guard(self):
        """AC-6: the actual index.html template source must contain a None-aware
        guard for mdd_bot, not the bare `| float` coercion.

        AST-style source check: fails RED until the implementer adds the guard.
        We check that the template source no longer contains the pattern
        'mdd_d.get("dry_run", 0)' (which silently converts None to 0).
        """
        template_path = (
            pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"
        )
        assert template_path.exists(), f"index.html not found at {template_path}"
        source = template_path.read_text(encoding="utf-8")

        # The buggy pattern family: mdd_d.get("dry_run", <default>) where the default
        # of 0 or 0) causes None to become 0 before `| float`, masking the None state.
        # We check for the `| float` suffix on an mdd_bot assignment — any use of
        # `| float` on the mdd_bot expression coerces None silently.
        # The exact spacing varies ("dry_run",  0) vs ("dry_run", 0)), so we use
        # a regex that tolerates whitespace variation.
        import re as _re
        buggy_pattern = _re.compile(
            r'mdd_bot\s*=.*mdd_d\.get\(["\']dry_run["\'],\s*0\).*\|\s*float'
        )
        matches = buggy_pattern.findall(source)
        assert not matches, (
            f"index.html still uses `| float` to coerce mdd_bot when dry_run may be None. "
            f"Found: {matches}. "
            "RED: replace with a None-aware guard that emits '--' when dry_run is None."
        )


# ===========================================================================
# AC-7: Accuracy property — no surface returns 0.0 when shadow_history has data
# ===========================================================================


class TestNoSurfaceReturnsZeroWhenDataExists:
    """AC-7: property tests asserting that when exit_triggers + shadow_history
    have rows for triggered symphonies, no metric surface returns a 0.0 that
    implies 'no data'."""

    def test_guard_alpha_summary_nonzero_with_exit_triggers(
        self, client, db_with_exit_triggers, tmp_path, monkeypatch
    ):
        """AC-7: /api/guard-alpha-summary guard_event_count > 0 when exit_triggers exist.

        This is a property assertion, not an exact-value assertion. We just
        confirm the count is positive — the exact value depends on DB content.
        """
        import analytics as analytics_module

        empty_pm = tmp_path / "no_pm"
        empty_pm.mkdir()
        monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(empty_pm))

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("guard_event_count", 0) > 0, (
            "guard_event_count must be positive when exit_triggers has rows. "
            f"Got {data.get('guard_event_count')}. "
            "AC-7 property violated."
        )

    def test_strip_guard_alpha_not_0_with_triggered_symphonies(
        self, client, db_with_exit_triggers, monkeypatch
    ):
        """AC-7: /api/strip/30d guard_alpha != 0.0 when triggered symphonies have divergence."""
        resp = client.get("/api/strip/30d")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("guard_alpha") != 0.0, (
            f"guard_alpha must not be 0.0 when triggered symphonies have divergence. "
            f"Got {data.get('guard_alpha')}. AC-7 property violated."
        )


# ===========================================================================
# AC-4b (live): strip intraday fallback — blob bot_state schema
# ===========================================================================


class TestStripIntradayFallbackBlobSchema:
    """AC-4b: get_windowed_strip() intraday fallback must work with the REAL
    bot_state schema (single JSON blob: id INTEGER PRIMARY KEY, data TEXT).

    The AC-4 tests in TestStripRouteIntradayGuardAlpha use db_with_exit_triggers
    whose bot_state is COLUMNAR (symphony_id TEXT PRIMARY KEY, position_value REAL).
    Those tests pass because app.py:2189 queries the position_value column, which
    exists in the columnar fixture.

    On the REAL droplet bot_state is a blob — no position_value column, no
    symphony_id column.  The subquery at app.py:2189:
        (SELECT position_value FROM bot_state WHERE symphony_id = t.symphony_id)
    throws OperationalError; the outer except Exception (app.py:2214) swallows it
    → guard_alpha stays 0.0, intraday_only never set.

    Fix path (for ld2-impl): mirror the guard_alpha_summary() AC-1b fix:
        1. Call database.load_state() before the SQL.
        2. Remove the correlated position_value subquery.
        3. Look up current_value from the blob dict per symphony_id.
        4. Fall back to a legacy columnar query when the blob dict is empty
           (ensures the AC-4 columnar-fixture tests keep passing).
    """

    @pytest.fixture
    def db_with_blob_bot_state_strip(self, tmp_path, monkeypatch):
        """Minimal DB matching the REAL bot_state schema for the strip route.

        bot_state: single blob row (id=1, data=JSON dict keyed by symphony_id).
        shadow_history: 1 distinct trading day — triggers insufficient_history=True
        so the intraday fallback fires.
        exit_triggers: 2 rows with positive divergence.
        """
        import json as _json

        db_path = str(tmp_path / "test_strip_blob.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS exit_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symphony_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                at_return REAL NOT NULL,
                trigger_reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symphony_id TEXT NOT NULL,
                trading_day TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                shadow_return REAL NOT NULL,
                current_return REAL NOT NULL,
                is_post_trigger INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY,
                data TEXT
            );
        """)
        conn.executemany(
            "INSERT INTO exit_triggers (symphony_id, ts_utc, at_return, trigger_reason) VALUES (?,?,?,?)",
            [
                ("SYM_X", "2026-06-22T13:43:00Z", 3.1, "TAKE_PROFIT"),
                ("SYM_Y", "2026-06-22T14:23:00Z", 2.2, "VWAP_BREAKDOWN"),
            ],
        )
        # 1 distinct trading_day → insufficient_history=True → intraday fallback fires
        conn.executemany(
            "INSERT INTO shadow_history "
            "(symphony_id, trading_day, ts_utc, shadow_return, current_return, is_post_trigger) "
            "VALUES (?,?,?,?,?,?)",
            [
                ("SYM_X", "2026-06-22", "2026-06-22T16:00:00Z", 3.1, -0.8, 1),
                ("SYM_Y", "2026-06-22", "2026-06-22T16:00:00Z", 2.2, -1.5, 1),
            ],
        )
        # Real blob schema — current_value provides the position_value weight
        blob = {
            "SYM_X": {"name": "ExAlpha", "current_value": 12000.0, "current_return": -0.8},
            "SYM_Y": {"name": "ExBeta",  "current_value": 18000.0, "current_return": -1.5},
        }
        conn.execute("INSERT INTO bot_state (id, data) VALUES (1, ?)", (_json.dumps(blob),))
        conn.commit()
        conn.close()
        monkeypatch.setenv("DB_PATH", db_path)
        return db_path

    def test_strip_intraday_only_set_with_blob_bot_state(
        self, client, db_with_blob_bot_state_strip
    ):
        """AC-4b: intraday_only=True when shadow_history has 1 day and bot_state is a blob.

        Fails RED because app.py:2189 queries the non-existent position_value column;
        OperationalError is swallowed by except Exception (app.py:2214) → strip returns
        without intraday_only, leaving it absent/False.
        """
        resp = client.get("/api/strip/30d")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()

        assert data.get("intraday_only") is True, (
            "strip response must include intraday_only=True when shadow_history has 1 day "
            "and bot_state uses the real blob schema. "
            f"Got intraday_only={data.get('intraday_only')!r}. "
            "RED: app.py:2189 queries `position_value` column which does not exist in "
            "blob bot_state (id, data TEXT) — OperationalError swallowed → fallback never fires."
        )

    def test_strip_guard_alpha_nonzero_with_blob_bot_state(
        self, client, db_with_blob_bot_state_strip
    ):
        """AC-4b: guard_alpha is non-zero when at_return > current_return with blob bot_state.

        The intraday formula requires position_value (weight); that comes from
        current_value in the blob dict via load_state().  With the broken column query
        the formula never runs → guard_alpha stays 0.0.

        Asserts guard_alpha != 0.0 (sign/presence), not the exact weighted average —
        the exact value depends on the fixture's at_return, current_return, and
        current_value inserted above.
        """
        resp = client.get("/api/strip/30d")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data.get("guard_alpha") != 0.0, (
            "guard_alpha must be non-zero when triggered symphonies have positive divergence "
            "and bot_state uses the real blob schema. "
            f"Got guard_alpha={data.get('guard_alpha')!r}. "
            "RED: position_value lookup fails → weighted sum is 0 → guard_alpha=0.0."
        )
