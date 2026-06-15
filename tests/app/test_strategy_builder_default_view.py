"""Regression + SPA-port: default Strategy Builder view must show stored proposals
via the unified SPA (/ai-advisor), not the deleted standalone page.

SPA-PORT UPDATE (cycle/spa-port-strategy-builder):
  The original tests asserted GET /ai-advisor/strategy-builder returns 200 and
  renders template_id values. After SPA-port, the GET route returns 302 and the
  content lives in the 6th tab of /ai-advisor. Tests updated accordingly.

Original regression (pre-SPA-port): found by live-daemon verification 2026-06-12:
  the GET route's docstring claimed 'empty string returns all advisory-only rows',
  but get_advisor_observations_for_symphony('') returns nothing — default page
  ALWAYS rendered the empty state. Fix was to call get_advisor_observations_for_role
  on the all-symphonies path.

Post-SPA-port: these tests verify the /ai-advisor route carries STRATEGY_BUILDER
observations to the unified template (same data, new delivery vehicle).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    import database

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_path))
    database.init_db()
    database.run_migrations()

    raw = {
        "objective": "diversify",
        "template_id": "T1_equal_weight",
        "rules_text": "WEIGHT-EQUAL\n  ASSET SPY",
        "candidate_id": "cand:T1",
        "gate_decision": "ADOPT_CANDIDATE",
        "n_candidates": 3,
        "fdr_q": 0.05,
        "fdr_adjusted_threshold": 0.027,
        "caveats": [],
        "cagr": 0.08,
        "sharpe": 1.2,
        "calmar": 1.0,
        "max_drawdown": -0.1,
    }
    database.insert_advisor_observation(
        advisor_role="STRATEGY_BUILDER",
        subject_type="strategy_proposal",
        subject_id="cand:T1",
        verdict="ADOPT_CANDIDATE",
        symphony_id="Alpha Symphony",
        raw_response=raw,
    )
    database.insert_advisor_observation(
        advisor_role="STRATEGY_BUILDER",
        subject_type="strategy_proposal",
        subject_id="cand:T1b",
        verdict="ADOPT_CANDIDATE",
        symphony_id="Beta Symphony",
        raw_response=dict(raw, candidate_id="cand:T1b", template_id="T6_momentum_top_n"),
    )
    # A non-STRATEGY_BUILDER row that must NOT leak into the page (role filter).
    database.insert_advisor_observation(
        advisor_role="OVERFITTING_CONSCIENCE",
        subject_type="autotune_run",
        subject_id="run:1",
        verdict="OK",
        symphony_id="Alpha Symphony",
        raw_response={"summary": "OC_ROW_MARKER"},
    )
    return db_path


@pytest.fixture
def client(seeded_db):
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestDefaultPageShowsAllProposals:
    def test_default_get_redirects_to_spa(self, client):
        """SPA-port: GET /ai-advisor/strategy-builder (no params) must redirect to /ai-advisor.

        OLD: returned 200 with standalone template rendering all STRATEGY_BUILDER rows.
        NEW: returns 302 to /ai-advisor — content is in the 6th tab panel (AC3).
        """
        resp = client.get("/ai-advisor/strategy-builder")
        assert resp.status_code in (301, 302, 303, 308), (
            f"GET /ai-advisor/strategy-builder returned {resp.status_code}; "
            "expected a redirect after SPA-port. "
            "The route must redirect to /ai-advisor (AC3)."
        )

    def test_spa_route_renders_strategy_builder_rows_from_all_symphonies(self, client):
        """SPA-port: GET /ai-advisor must render STRATEGY_BUILDER rows from all symphonies.

        The original regression (default page always empty) is now tested against
        the /ai-advisor route which must prefetch STRATEGY_BUILDER observations.
        The template_id values from the seeded DB must appear in /ai-advisor's response.

        RED: current /ai-advisor route does not prefetch strategy builder rows.
        """
        import app as _am
        from unittest.mock import MagicMock, patch

        _am.app.config["TESTING"] = True

        fake_corr = MagicMock()
        fake_corr.compute_pairwise_correlations.return_value = []
        fake_corr.CRISIS_CAVEAT = "caveat"

        with patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}):
            resp = client.get("/ai-advisor")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # The seeded DB has T1_equal_weight and T6_momentum_top_n for STRATEGY_BUILDER.
        # These must appear in /ai-advisor if the route correctly prefetches the observations.
        assert "T1_equal_weight" in html or "T6_momentum_top_n" in html, (
            "GET /ai-advisor must render stored STRATEGY_BUILDER proposals from the seeded DB. "
            "The route must call get_advisor_observations_for_role('STRATEGY_BUILDER') "
            "and pass the observations to the unified template (AC2, AC4). "
            "This is the SPA-port version of the original 'default page always empty' regression."
        )

    def test_spa_strategy_builder_panel_uses_role_scoped_accessor(self, client):
        """SPA-port: GET /ai-advisor must call get_advisor_observations_for_role('STRATEGY_BUILDER').

        The role filter is enforced at the DB accessor layer: the /ai-advisor route
        uses database.get_advisor_observations_for_role("STRATEGY_BUILDER"), which
        only returns STRATEGY_BUILDER rows. Non-SB roles (OC, SC, etc.) can appear
        elsewhere on the page (their own panels) — but the SB panel is fed only by
        the role-scoped accessor call.

        We verify the accessor call contract (not full-page HTML) to avoid false
        positives from legitimate OC/SC observations in the Overview panel.
        """
        import app as _am
        from unittest.mock import MagicMock, patch

        _am.app.config["TESTING"] = True

        called_roles: list = []

        def _capture_role(role, limit=50):
            called_roles.append(role)
            return []

        fake_corr = MagicMock()
        fake_corr.compute_pairwise_correlations.return_value = []
        fake_corr.CRISIS_CAVEAT = "caveat"

        with (
            patch.object(
                _am.database, "get_advisor_observations_for_role", side_effect=_capture_role
            ),
            patch.object(_am.analytics, "get_history_with_cache_invalidation", return_value={}),
            patch.object(_am.analytics, "list_available_symphonies", return_value=[]),
            patch.object(_am.analytics, "compute_per_symphony_returns", return_value=([], [], [])),
            patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}),
        ):
            resp = client.get("/ai-advisor")

        assert resp.status_code == 200
        assert "STRATEGY_BUILDER" in called_roles, (
            f"GET /ai-advisor did not call get_advisor_observations_for_role('STRATEGY_BUILDER'). "
            f"Roles called: {called_roles}. "
            "Role isolation for the strategy-builder panel requires the route to use "
            "the role-scoped accessor with 'STRATEGY_BUILDER'."
        )

    def test_symphony_param_scopes_via_redirect(self, client):
        """SPA-port: GET /ai-advisor/strategy-builder?symphony_id=X must still redirect.

        The redirect must apply regardless of query parameters — no 200 response.
        The symphony_id param may be passed along in the redirect Location header,
        or dropped (either is acceptable — the tab state can be managed client-side).
        """
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=Alpha+Symphony")
        assert resp.status_code in (301, 302, 303, 308), (
            f"GET /ai-advisor/strategy-builder?symphony_id=... returned {resp.status_code}; "
            "expected redirect. Query parameters must not bypass the redirect (AC3)."
        )

    def test_accessor_role_scoped_read_is_read_only(self, seeded_db):
        """The new accessor must use the RO connection (architecture constraint 5)."""
        import inspect

        import database

        src = inspect.getsource(database.get_advisor_observations_for_role)
        assert "get_ro_connection" in src


class TestDashboardLoadsDotenv:
    """app.py must load .env into os.environ for the dashboard process.

    The dashboard spawns alpha_bot_execution.py as a subprocess and never
    imports it, so its load_dotenv never runs here. Before the fix,
    ai_advisor._build_client (chat + LLM suggestions) silently degraded to
    'no API key' on any daemon not launched from a key-exporting shell.
    Found by live-daemon verification with real credentials (2026-06-12).
    """

    def test_app_calls_load_dotenv_on_env_file_path(self):
        src = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
        assert "load_dotenv(ENV_FILE_PATH)" in src
