"""Regression: default Strategy Builder page (no symphony_id param) must show
stored proposals — through the REAL database accessor, no mocks.

Found by live-daemon verification 2026-06-12: the GET route's docstring claimed
'empty string returns all advisory-only rows', but
get_advisor_observations_for_symphony('') does WHERE symphony_id = '' and
returns nothing — so the default page load ALWAYS rendered the empty state.
Every prior route test mocked the accessor, encoding the docstring's false
claim into the mock. This module deliberately uses a real seeded SQLite DB.
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
    def test_default_get_renders_rows_from_all_symphonies(self, client):
        """No symphony_id param → all STRATEGY_BUILDER rows, across symphonies."""
        resp = client.get("/ai-advisor/strategy-builder")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "T1_equal_weight" in html, (
            "Default page load must render stored proposals; rendering the "
            "empty state while rows exist was the live-verification bug"
        )
        assert "T6_momentum_top_n" in html, "Rows from every symphony must appear"

    def test_default_get_excludes_other_advisor_roles(self, client):
        resp = client.get("/ai-advisor/strategy-builder")
        assert "OC_ROW_MARKER" not in resp.get_data(as_text=True), (
            "Role filter must hold on the all-symphonies path"
        )

    def test_symphony_param_still_scopes(self, client):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=Alpha+Symphony")
        html = resp.get_data(as_text=True)
        assert "T1_equal_weight" in html
        assert "T6_momentum_top_n" not in html, (
            "Explicit symphony_id must still scope to that symphony only"
        )

    def test_accessor_role_scoped_read_is_read_only(self, seeded_db):
        """The new accessor must use the RO connection (architecture constraint 5)."""
        import inspect

        import database

        src = inspect.getsource(database.get_advisor_observations_for_role)
        assert "get_ro_connection" in src
