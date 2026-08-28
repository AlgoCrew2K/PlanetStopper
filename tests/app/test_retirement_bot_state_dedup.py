"""RED tests -- Cycle 2d AC-4: load_state dedup on the approved-card panel
path (PR#140 2nd /code-review finding 5).

feature-plans/retirement-approval-polish-2d.md AC-4: on the AI Advisor panel
render with an approved retirement card, database.load_state() must run at
MOST ONCE -- _refresh_retirement_display_names's bot_state need must thread
onto the SAME shared _ensure_ai_advisor_bot_state() lazy closure the
checklist/frontrunner blocks already use, instead of independently calling
database.load_state() a second time.

Today (pre-fix): an approved retirement card triggers TWO independent
database.load_state() calls on one request --
  1. _fetch_retirement_recommendations() -> _refresh_retirement_display_names()
     calls database.load_state() itself (module-level function, no access to
     ai_advisor_tab()'s local closure).
  2. The checklist-assembly block (_ret_any_approved gate) calls
     _ensure_ai_advisor_bot_state(), which loads AGAIN since the closure's
     own "already loaded" flag was never set by call #1.

Two complementary proofs, per the team-lead's explicit design:
  (a) Direct: 1 approved rec, 0 frontrunner proposals -> exactly ONE
      load_state() call (the checklist + name-refresh consumers share one
      closure call).
  (b) Robustness (team-lead's specified technique): proven DIFFERENTIALLY
      against a frontrunner-only baseline (1 pending frontrunner proposal, 0
      retirement recs) so the test is immune to however many OTHER
      load_state() consumers exist elsewhere on the page -- adding an
      approved retirement card on TOP of that baseline must not increase the
      call count (== baseline, not baseline+1). Mirrors the established
      differential-counting pattern in tests/app/test_retirement_display_
      names.py::test_load_state_call_count_does_not_scale_with_recommendation_count.

Fixture/helper pattern duplicated from sibling retirement test files per
this repo's established fixtures-are-not-cross-file-shared convention.

Expected state: RED until app.py threads _refresh_retirement_display_names's
bot_state need onto the shared _ensure_ai_advisor_bot_state() closure.
"""

from __future__ import annotations

import pytest

_CANDIDATE_HASH = "cand-dedup-aaa"
_CANDIDATE_NAME = "Momentum Alpha Rotation"
_SIBLING_HASH = "sib-dedup-bbb"
_SIBLING_NAME = "Value Tilt Core"


def _sample_raw_response(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH):
    return {
        "candidate_id": candidate_id,
        "sibling_id": sibling_id,
        "correlation": 0.80,
        "ci_lower": 0.72,
        "ci_upper": 0.86,
        "n_obs": 200,
        "candidate_composite": 0.20,
        "sibling_composite": 0.75,
        "candidate_metrics": {
            "annualized_return": 0.03,
            "sharpe": 0.2,
            "sortino": 0.25,
            "max_drawdown": -0.30,
            "calmar": 0.10,
        },
        "sibling_metrics": {
            "annualized_return": 0.18,
            "sharpe": 1.4,
            "sortino": 1.8,
            "max_drawdown": -0.06,
            "calmar": 3.0,
        },
        "uncertainty_gate_passed": True,
        "structural_redundancy_gate_passed": True,
        "stressed_correlation": 0.78,
        "holdings_overlap": None,
        "basis_label": "actual-traded (bot) daily returns",
        "explanation": "A concise explanation.",
    }


def _seed_recommendation(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH):
    import database as db_module

    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=_sample_raw_response(candidate_id, sibling_id),
    )


def _seed_named_roster(id_to_name: dict):
    import database as db_module

    roster = {sid: {"name": name, "logic_holdings": {}} for sid, name in id_to_name.items()}
    db_module.save_state(roster)


def _make_counting_load_state(real_load_state, counts, key):
    def _counting(*args, **kwargs):
        counts[key] = counts.get(key, 0) + 1
        return real_load_state(*args, **kwargs)

    return _counting


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database as db_module
    from database import init_db, run_migrations

    db_path = str(tmp_path / "test_retirement_bot_state_dedup.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


class TestLoadStateCalledExactlyOnceForApprovedCard:
    def test_load_state_called_exactly_once_with_no_frontrunner_rows(
        self, client, isolated_db, monkeypatch
    ):
        """Direct proof: with ONE approved retirement rec (needing bot_state
        for BOTH the checklist AND display-name refresh) and ZERO frontrunner
        proposals (so the frontrunner block contributes no load_state() calls
        of its own), the retirement panel's two bot_state consumers must
        share ONE load_state() call, not two."""
        import database as db_module

        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation()
        db_module.upsert_retirement_decision(_CANDIDATE_HASH, approval_status="approved")

        real_load_state = db_module.load_state
        counts: dict[str, int] = {}
        monkeypatch.setattr(
            db_module, "load_state", _make_counting_load_state(real_load_state, counts, "n")
        )

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200

        assert counts.get("n", 0) == 1, (
            f"Expected exactly ONE database.load_state() call for an approved "
            f"retirement card's panel render (checklist + display-name refresh "
            f"sharing one closure), got {counts.get('n', 0)}."
        )


class TestLoadStateCallCountMatchesFrontrunnerOnlyBaseline:
    def test_approved_card_does_not_increase_call_count_beyond_frontrunner_baseline(
        self, tmp_path, monkeypatch
    ):
        """Robustness proof (differential): proven against a frontrunner-only
        baseline so this test is immune to however many OTHER load_state()
        consumers exist elsewhere on the page -- adding an APPROVED
        retirement card on top of that SAME baseline must not increase the
        call count."""
        import app as app_module
        import database as db_module
        from database import init_db, run_migrations

        real_load_state = db_module.load_state

        # --- Baseline: one pending frontrunner proposal, ZERO retirement recs. ---
        db_path_baseline = str(tmp_path / "baseline.db")
        monkeypatch.setattr(db_module, "DB_FILE", db_path_baseline)
        init_db()
        run_migrations()
        db_module.insert_frontrunner_proposal(
            symphony_id="sym-baseline",
            proposal_source="frontrunner_builder",
            candidate_tree={"step": "root"},
        )

        app_module.app.config["TESTING"] = True
        counts_baseline: dict[str, int] = {}
        monkeypatch.setattr(
            db_module,
            "load_state",
            _make_counting_load_state(real_load_state, counts_baseline, "n"),
        )
        with app_module.app.test_client() as c:
            resp_baseline = c.get("/ai-advisor")
        assert resp_baseline.status_code == 200
        baseline_n = counts_baseline.get("n", 0)
        assert baseline_n > 0, (
            "Expected the frontrunner-only baseline to call load_state() at least once."
        )

        # --- Scenario: SAME frontrunner proposal PLUS one approved retirement
        # rec, on a fresh isolated DB (mirrors test_retirement_display_names.py's
        # own Scenario-A/B counting-wrapper pattern). ---
        db_path_scenario = str(tmp_path / "scenario.db")
        monkeypatch.setattr(db_module, "DB_FILE", db_path_scenario)
        init_db()
        run_migrations()
        db_module.insert_frontrunner_proposal(
            symphony_id="sym-baseline",
            proposal_source="frontrunner_builder",
            candidate_tree={"step": "root"},
        )
        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        _seed_recommendation()
        db_module.upsert_retirement_decision(_CANDIDATE_HASH, approval_status="approved")

        counts_scenario: dict[str, int] = {}
        monkeypatch.setattr(
            db_module,
            "load_state",
            _make_counting_load_state(real_load_state, counts_scenario, "n"),
        )
        with app_module.app.test_client() as c:
            resp_scenario = c.get("/ai-advisor")
        assert resp_scenario.status_code == 200
        scenario_n = counts_scenario.get("n", 0)

        assert scenario_n == baseline_n, (
            f"Adding an approved retirement card increased database.load_state() call "
            f"count from {baseline_n} (frontrunner-only baseline) to {scenario_n} -- "
            "AC-4 requires the shared closure to absorb it, not add a redundant "
            "second call."
        )
