"""
RED tests -- GET /api/retirement-recommendations (AC-9).

The route does not exist yet; every test in this file is expected to fail RED
(404, or an AttributeError from monkeypatching a not-yet-present
database.get_advisor_observations_for_role usage inside the route) until the
implementer adds it. Directly mirrors tests/app/test_incubation_route.py's
structure (the plan's own architecture note: "mirrors api_incubation").

THE CROSS-IMPLEMENTER CONTRACT (PM mandate): the raw_response schema is
defined ONCE in tests/advisors/test_retirement_recommender_persistence.py and
reused IDENTICALLY here. This route's JSON response, for each recommendation,
must surface (at minimum) those same literal field names -- retire-route
renders what retire-math produces, never a divergent/renamed shape.

Contract under test (pinned in .claude/tdd-handoff.md "app.py -- route"):
- Read-only; global auth hook applies; NOT in _SETTINGS_WRITE_ALLOWLIST; no
  LIVE_EXECUTION interaction.
- Reads the latest persisted RETIREMENT_RECOMMENDATION advisor_observations
  rows via database.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", ...)
  (test-writer ruling: this is the real accessor that exists for a role-wide,
  multi-subject read -- get_advisor_observations_for_subject is scoped to ONE
  subject_id and cannot list "all current recommendations across candidates").
- Empty -> {"recommendations": []}, never a 500.
- NaN/Infinity in any numeric field sanitized to null before jsonify.
- GET never writes (no persistence call reachable from this route).
- PR-level /code-review Finding 4 (PM ruling): the nightly 03:45 tick calls
  build_recommendations()+persist_recommendations() EVERY night into an
  APPEND-ONLY table, so a multi-night deployment accumulates one row per
  night per still-flagged pair, plus stale rows for pairs no longer flagged.
  _fetch_retirement_recommendations() must filter to ONLY the rows sharing
  the MOST RECENT row's calendar date (the same substr(created_at,1,10)
  trick database.get_candidate_alert_last_run already established) -- never
  the newest-N rows verbatim across multiple nights. See
  TestFetchRetirementRecommendationsLatestBatchOnly below.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import advisors.tenure_review as tenure_review
import app as app_module
import database as db_module
from database import init_db, run_migrations
from tests.app import _tenure_review_panel_builders as pb

_RAW_RESPONSE_KEYS = {
    "candidate_id",
    "sibling_id",
    "correlation",
    "ci_lower",
    "ci_upper",
    "n_obs",
    "candidate_composite",
    "sibling_composite",
    "candidate_metrics",
    "sibling_metrics",
    "uncertainty_gate_passed",
    "structural_redundancy_gate_passed",
    "stressed_correlation",
    "holdings_overlap",
    "basis_label",
}


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_recommendations_route.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _sample_raw_response(candidate_id="cand-sym", sibling_id="sib-sym"):
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
    }


def _seed_recommendation(candidate_id="cand-sym", sibling_id="sib-sym"):
    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=_sample_raw_response(candidate_id, sibling_id),
    )


def _seed_recommendation_backdated(candidate_id: str, sibling_id: str, days_ago: int) -> None:
    """Insert a RETIREMENT_RECOMMENDATION row with an explicit, backdated
    created_at -- insert_advisor_observation has no created_at override (it
    always uses the schema's DEFAULT (datetime('now'))), so a raw SQL insert
    is required to simulate "an older night's batch". Mirrors
    tests/database/test_033_candidate_alert_state.py's
    test_older_batch_does_not_inflate_latest_run -- the same established
    idiom for backdating advisor_observations.created_at in this codebase."""
    old_date = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db_module.get_connection()
    try:
        conn.execute(
            "INSERT INTO advisor_observations "
            "(advisor_role, subject_type, subject_id, verdict, raw_response, "
            " is_advisory_only, symphony_id, created_at) "
            "VALUES ('RETIREMENT_RECOMMENDATION', 'symphony', ?, 'retire_candidate', "
            " ?, 1, ?, ?)",
            (
                candidate_id,
                json.dumps(_sample_raw_response(candidate_id, sibling_id)),
                candidate_id,
                old_date,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PR-level /code-review Finding 4 (app.py's _fetch_retirement_recommendations,
# and the 03:45 nightly tick): the nightly tick calls
# build_recommendations()+persist_recommendations() EVERY night, and
# advisor_observations is APPEND-ONLY (never updated/deleted) -- so a
# multi-night deployment accumulates one row per night per still-flagged
# pair, and a prior night's candidate that's no longer flagged tonight still
# has its old row sitting in the table. The original _fetch_retirement_
# recommendations returned the newest-N rows VERBATIM (whatever
# get_advisor_observations_for_role's default limit/ordering produced),
# which folds multiple nights together -- duplicate/stale cards. PM ruling:
# filter to ONLY the rows sharing the MOST RECENT row's calendar date (the
# same substr(created_at,1,10)-equals-MAX(...) trick already established by
# database.get_candidate_alert_last_run for an analogous "one batch, not the
# whole table" read).
# ---------------------------------------------------------------------------


class TestFetchRetirementRecommendationsLatestBatchOnly:
    def test_fetch_returns_only_the_latest_nights_rows_not_a_stale_prior_night(self, isolated_db):
        """THE load-bearing pin for Finding 4: two nights of rows persisted;
        only the latest night's candidates must be returned. Currently RED
        (the un-fixed fetch returns both nights' rows verbatim)."""
        _seed_recommendation_backdated("old-night-cand", "old-night-sib", days_ago=1)
        _seed_recommendation("new-night-cand", "new-night-sib")

        result = app_module._fetch_retirement_recommendations()
        candidate_ids = {r.get("candidate_id") for r in result}

        assert "new-night-cand" in candidate_ids, (
            "The latest night's real recommendation must be present."
        )
        assert "old-night-cand" not in candidate_ids, (
            f"A prior night's stale recommendation ('old-night-cand', "
            f"backdated 1 day) leaked into the fetch alongside tonight's "
            f"batch: {candidate_ids!r}. _fetch_retirement_recommendations "
            "must return ONLY the rows sharing the most recent row's "
            "calendar date, not the newest-N rows verbatim across multiple "
            "nights (PR-level /code-review Finding 4)."
        )

    def test_route_response_excludes_a_stale_prior_night_batch(self, client, isolated_db):
        """Same invariant, exercised through the real HTTP route (not just the
        helper function directly) -- proves the fix is actually wired into
        the response path a real dashboard load would hit."""
        _seed_recommendation_backdated("old-night-cand-2", "old-night-sib-2", days_ago=3)
        _seed_recommendation("new-night-cand-2", "new-night-sib-2")

        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        candidate_ids = {r.get("candidate_id") for r in data["recommendations"]}

        assert "new-night-cand-2" in candidate_ids
        assert "old-night-cand-2" not in candidate_ids, (
            f"GET /api/retirement-recommendations surfaced a 3-day-stale "
            f"recommendation alongside tonight's batch: {candidate_ids!r}."
        )

    def test_same_candidate_persisted_two_nights_returns_only_the_latest_row(self, isolated_db):
        """A pair still flagged on consecutive nights produces a genuine
        DUPLICATE row (advisor_observations is append-only -- the nightly
        tick never upserts). The fetch must surface the candidate only ONCE
        (the latest night's row), not once per historical night it was ever
        flagged."""
        _seed_recommendation_backdated("repeat-cand", "repeat-sib", days_ago=1)
        _seed_recommendation("repeat-cand", "repeat-sib")

        result = app_module._fetch_retirement_recommendations()
        matching = [r for r in result if r.get("candidate_id") == "repeat-cand"]
        assert len(matching) == 1, (
            f"'repeat-cand' was flagged on 2 consecutive nights (append-only "
            f"duplicate rows) but appeared {len(matching)} times in the "
            f"fetch result -- expected exactly 1 (the latest night's row "
            "only)."
        )


# ---------------------------------------------------------------------------
# Route shape
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteShape:
    def test_returns_200(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200, (
            f"Expected 200 from GET /api/retirement-recommendations, got "
            f"{resp.status_code}. Route is not yet implemented -- RED."
        )

    def test_content_type_is_json(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        assert "application/json" in resp.content_type

    def test_response_has_recommendations_key(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)


# ---------------------------------------------------------------------------
# Empty / degraded states
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteEmptyState:
    def test_no_recommendations_returns_empty_list_not_error(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["recommendations"] == []

    def test_read_failure_degrades_never_500(self, client, isolated_db, monkeypatch):
        monkeypatch.setattr(
            db_module,
            "get_advisor_observations_for_role",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated DB failure")),
        )
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code != 500, (
            "A read failure must degrade honestly, never 500 the whole route."
        )


# ---------------------------------------------------------------------------
# Per-recommendation content: the authoritative raw_response schema surfaces
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteContent:
    def test_recommendation_row_surfaces_the_authoritative_field_names(self, client, isolated_db):
        _seed_recommendation(candidate_id="cand-sym-1", sibling_id="sib-sym-1")
        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        row = next(r for r in data["recommendations"] if r.get("candidate_id") == "cand-sym-1")
        missing = _RAW_RESPONSE_KEYS - row.keys()
        assert not missing, (
            f"GET /api/retirement-recommendations response row is missing the "
            f"authoritative raw_response fields: {missing}. The route must "
            "render the SAME literal key names retirement_recommender.py "
            "persists (see the raw_response schema in "
            "tests/advisors/test_retirement_recommender_persistence.py)."
        )

    def test_recommendation_row_values_match_the_persisted_raw_response(self, client, isolated_db):
        _seed_recommendation(candidate_id="cand-sym-2", sibling_id="sib-sym-2")
        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        row = next(r for r in data["recommendations"] if r.get("candidate_id") == "cand-sym-2")
        assert row["sibling_id"] == "sib-sym-2"
        assert row["correlation"] == pytest.approx(0.80)
        assert row["n_obs"] == 200
        assert row["candidate_metrics"]["annualized_return"] == pytest.approx(0.03)

    def test_multiple_recommendations_all_present(self, client, isolated_db):
        _seed_recommendation(candidate_id="cand-a", sibling_id="sib-a")
        _seed_recommendation(candidate_id="cand-b", sibling_id="sib-b")
        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        candidate_ids = {r.get("candidate_id") for r in data["recommendations"]}
        assert {"cand-a", "cand-b"} <= candidate_ids


# ---------------------------------------------------------------------------
# Strict-JSON safety (NaN/Infinity)
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteStrictJson:
    def test_no_nan_or_infinity_in_numeric_fields(self, client, isolated_db, monkeypatch):
        _seed_recommendation(candidate_id="cand-nan", sibling_id="sib-nan")

        real_getter = db_module.get_advisor_observations_for_role

        def _poisoned(role, *a, **k):
            rows = real_getter(role, *a, **k)
            for row in rows:
                if row.get("symphony_id") == "cand-nan":
                    row["raw_response"]["correlation"] = float("nan")
            return rows

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", _poisoned)

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        raw_body = resp.get_data(as_text=True)
        assert "NaN" not in raw_body and "Infinity" not in raw_body, (
            f"Response body contains a bare NaN/Infinity token (invalid JSON) -- "
            f"must be sanitized to null. Body: {raw_body[:300]}"
        )
        data = resp.get_json()
        row = next(r for r in data["recommendations"] if r.get("candidate_id") == "cand-nan")
        val = row.get("correlation")
        if isinstance(val, float):
            assert not math.isnan(val)


# ---------------------------------------------------------------------------
# GET never writes
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteNeverWrites:
    def test_get_does_not_call_insert_advisor_observation(self, client, isolated_db, monkeypatch):
        def _explode(**kwargs):
            raise AssertionError("GET /api/retirement-recommendations must never write")

        monkeypatch.setattr(db_module, "insert_advisor_observation", _explode)
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200

    def test_get_does_not_change_advisor_observations_row_count(self, client, isolated_db):
        before = db_module.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", limit=50)
        client.get("/api/retirement-recommendations")
        client.get("/api/retirement-recommendations")
        after = db_module.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", limit=50)
        assert len(before) == len(after) == 0


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-pw-retirement"
_TEST_SECRET_KEY = "test-secret-retirement"


@pytest.fixture()
def auth_client_no_session(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestRetirementRecommendationsRouteAuthGate:
    def test_unauthenticated_xhr_returns_401(self, auth_client_no_session, isolated_db):
        resp = auth_client_no_session.get(
            "/api/retirement-recommendations", headers={"X-Requested-With": "XMLHttpRequest"}
        )
        assert resp.status_code == 401, (
            f"Unauthenticated XHR to /api/retirement-recommendations must return "
            f"401, got {resp.status_code}. If 404: route not yet implemented "
            "(RED). If 200/302: route is missing the auth gate."
        )


# ---------------------------------------------------------------------------
# Not a settings-write surface
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteNotWriteSurface:
    def test_route_path_not_in_settings_write_allowlist(self):
        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.skip("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        assert "/api/retirement-recommendations" not in allowlist


# ---------------------------------------------------------------------------
# The symphony tenure review on the API: the latest completed run, one entry per
# live symphony, joined to the operator's decisions. Run rows are produced by the
# real review compute (tests/app/_tenure_review_panel_builders.py) and stored the way
# the run pipeline stores them; the route only reads.
# ---------------------------------------------------------------------------

_ITEM_NAMES = ("performance", "composite", "redundancy", "backtest", "marginal", "long_vix")


def _get(client):
    resp = client.get("/api/retirement-recommendations")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
    return pb.strict_json(resp)


def _entries_by_id(data):
    return {e["symphony_id"]: e for e in data["review_entries"]}


class TestNoRunYet:
    def test_the_review_fields_are_empty_and_the_status_is_idle(self, client, isolated_db):
        data = _get(client)
        assert data["review_run"] is None
        assert data["review_entries"] == []
        assert data["recommendations"] == []
        status = data["review_status"]
        assert status["running"] is False
        assert status["running_since"] is None
        assert status["last_failed_attempt"] is None

    def test_existing_recommendations_stay_listed_next_to_the_empty_review(
        self, client, isolated_db
    ):
        _seed_recommendation("legacy-cand", "legacy-sib")
        data = _get(client)
        assert [r["candidate_id"] for r in data["recommendations"]] == ["legacy-cand"]
        assert data["review_entries"] == []
        assert data["review_run"] is None

    def test_a_row_that_is_not_a_run_record_degrades_without_a_500(self, client, isolated_db):
        db_module.insert_advisor_observation(
            advisor_role=tenure_review.TENURE_REVIEW_RUN_ROLE,
            subject_type="tenure_review_run",
            subject_id=pb.RUN_ID_FIRST,
            verdict="completed",
            raw_response="this is not json {",
        )
        _seed_recommendation("legacy-cand", "legacy-sib")
        data = _get(client)
        assert isinstance(data["review_entries"], list)
        assert "review_run" in data
        assert data["recommendations"] == [], (
            "a run row exists but cannot be read, so which recommendations belong to it is "
            "unknown: the list must be empty, never the latest-date list"
        )

    def test_a_read_failure_degrades_every_review_field_and_never_500s(
        self, client, isolated_db, monkeypatch
    ):
        pb.store_full_run()
        monkeypatch.setattr(
            db_module,
            "get_advisor_observations_for_role",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated DB failure")),
        )
        data = _get(client)
        assert data["review_run"] is None
        assert data["review_entries"] == []
        assert data["recommendations"] == []


class TestZeroLiveSymphonies:
    def test_a_completed_run_with_no_entries_returns_an_empty_list_and_status_200(
        self, client, isolated_db
    ):
        record = pb.empty_record()
        pb.store_run(record)
        data = _get(client)
        assert data["review_entries"] == []
        assert data["review_run"]["run_id"] == record["run_id"], (
            "an empty run is still the latest completed run, so its header must be present"
        )


class TestLatestRunIsReturnedWhole:
    def test_the_run_header_carries_every_run_field_but_not_the_entries(self, client, isolated_db):
        record = pb.store_full_run()
        data = _get(client)
        expected = {k: v for k, v in record.items() if k != "entries"}
        assert data["review_run"] == expected
        assert "entries" not in data["review_run"]

    def test_every_live_symphony_has_exactly_one_entry_equal_to_the_stored_one(
        self, client, isolated_db
    ):
        record = pb.store_full_run()
        data = _get(client)
        assert len(data["review_entries"]) == len(record["entries"])
        got = _entries_by_id(data)
        assert len(got) == len(record["entries"]), "a symphony appears more than once"
        for stored in record["entries"]:
            served = dict(got[stored["symphony_id"]])
            served.pop("approval_status")
            assert served == stored, stored["display_name"]

    def test_an_unknown_tenure_is_served_as_null_and_never_as_zero(self, client, isolated_db):
        pb.store_full_run()
        dateless = _entries_by_id(_get(client))[pb.FULL_IDS["dateless"]]
        assert dateless["tenure_trading_days"] is None
        assert dateless["eligible"] is False
        assert dateless["verdict"] is None
        assert "tenure unavailable" in dateless["eligibility_text"]

    def test_every_evidence_item_names_its_basis_window_subject_source_and_figure_units(
        self, client, isolated_db
    ):
        pb.store_full_run()
        for entry in _get(client)["review_entries"]:
            assert set(entry["evidence"]) == set(_ITEM_NAMES), entry["display_name"]
            for name, item in entry["evidence"].items():
                where = f"{entry['display_name']} / {name}"
                for key in ("status", "label", "window", "subject", "source", "figures"):
                    assert key in item, f"{where} lacks {key}"
                assert item["label"], where
                assert item["subject"], where
                assert item["source"], where
                for fig_name, fig in item["figures"].items():
                    assert fig.get("unit"), f"{where} / {fig_name} has no unit"
                    if fig["value"] is None:
                        assert fig.get("reason"), f"{where} / {fig_name} is null without a reason"

    def test_the_counterfactual_item_carries_its_own_label_and_the_realized_item_does_not(
        self, client, isolated_db
    ):
        pb.store_full_run()
        watch = _entries_by_id(_get(client))[pb.FULL_IDS["watch"]]
        assert "counterfactual" in watch["evidence"]["backtest"]["label"]
        assert "counterfactual" not in watch["evidence"]["performance"]["label"]
        assert "realized" in watch["evidence"]["performance"]["label"]

    def test_the_comparison_item_lists_the_excluded_symphony_by_display_name(
        self, client, isolated_db
    ):
        pb.store_run(pb.small_record())
        marginal = _entries_by_id(_get(client))[pb.SMALL_IDS["first"]]["evidence"]["marginal"]
        assert [x["display_name"] for x in marginal["excluded"]] == [pb.SMALL_NAMES["young"]]


class TestDecisionsJoinOnTheComposerHash:
    def test_an_entry_without_a_decision_row_reads_none_not_pending(self, client, isolated_db):
        pb.store_full_run()
        for entry in _get(client)["review_entries"]:
            assert entry["approval_status"] is None, entry["display_name"]

    def test_each_decided_symphony_carries_its_own_status_whatever_its_verdict(
        self, client, isolated_db
    ):
        pb.store_full_run()
        ids = pb.FULL_IDS
        db_module.upsert_retirement_decision(ids["retire"], approval_status="approved")
        db_module.upsert_retirement_decision(ids["keep"], approval_status="rejected")
        db_module.upsert_retirement_decision(ids["watch"], approval_status="approved")
        entries = _entries_by_id(_get(client))
        assert entries[ids["retire"]]["approval_status"] == "approved"
        assert entries[ids["keep"]]["approval_status"] == "rejected"
        assert entries[ids["watch"]]["approval_status"] == "approved", (
            "a decision made on an earlier verdict must show on a watch entry too"
        )
        assert entries[ids["young"]]["approval_status"] is None
        assert entries[ids["silent"]]["approval_status"] is None

    def test_a_stored_pending_decision_reads_pending_unlike_no_row(self, client, isolated_db):
        pb.store_full_run()
        db_module.upsert_retirement_decision(pb.FULL_IDS["keep"], approval_status="pending")
        entries = _entries_by_id(_get(client))
        assert entries[pb.FULL_IDS["keep"]]["approval_status"] == "pending"
        assert entries[pb.FULL_IDS["watch"]]["approval_status"] is None

    def test_a_decision_made_between_two_requests_shows_on_the_second(self, client, isolated_db):
        pb.store_full_run()
        first = _entries_by_id(_get(client))
        assert first[pb.FULL_IDS["retire"]]["approval_status"] is None
        db_module.upsert_retirement_decision(pb.FULL_IDS["retire"], approval_status="approved")
        second = _entries_by_id(_get(client))
        assert second[pb.FULL_IDS["retire"]]["approval_status"] == "approved"

    def test_a_failed_decision_read_degrades_every_entry_to_none(
        self, client, isolated_db, monkeypatch
    ):
        record = pb.store_full_run()
        monkeypatch.setattr(
            db_module,
            "get_retirement_decisions",
            lambda: (_ for _ in ()).throw(RuntimeError("simulated decision read failure")),
        )
        data = _get(client)
        assert len(data["review_entries"]) == len(record["entries"])
        assert all(e["approval_status"] is None for e in data["review_entries"])


class TestOnlyTheLatestRunIsShown:
    def _two_runs_on_one_date(self):
        first = pb.full_record(pb.RUN_ID_FIRST, completed_at="2026-12-31T03:50:00+00:00")
        first_text = "Explanation written by the first run."
        pb.set_explanation(first, pb.FULL_IDS["retire"], first_text, "2026-12-30")
        pb.store_recommendation(
            pb.recommendation_for(
                first,
                pb.entry_of(first, pb.FULL_IDS["retire"]),
                explanation=first_text,
                written_on="2026-12-30",
            )
        )
        pb.store_run(first)

        second = pb.full_record(
            pb.RUN_ID_SECOND, trigger="on_demand", completed_at="2026-12-31T21:10:00+00:00"
        )
        second_text = "Explanation written by the second run."
        pb.entry_of(second, pb.FULL_IDS["keep"])["display_name"] = "Yankee Tail Two Renamed"
        pb.set_explanation(second, pb.FULL_IDS["retire"], second_text, "2026-12-31")
        pb.store_recommendation(
            pb.recommendation_for(
                second,
                pb.entry_of(second, pb.FULL_IDS["retire"]),
                explanation=second_text,
                written_on="2026-12-31",
            )
        )
        pb.store_run(second)
        return first, second

    def test_a_nightly_and_a_later_on_demand_run_on_one_date_show_only_the_later(
        self, client, isolated_db
    ):
        _, second = self._two_runs_on_one_date()
        data = _get(client)
        assert data["review_run"]["run_id"] == second["run_id"]
        assert data["review_run"]["trigger"] == "on_demand"
        ids = [e["symphony_id"] for e in data["review_entries"]]
        assert len(ids) == len(set(ids)) == len(second["entries"]), (
            "each live symphony must appear once"
        )
        renamed = _entries_by_id(data)[pb.FULL_IDS["keep"]]["display_name"]
        assert renamed == "Yankee Tail Two Renamed"

    def test_the_recommendation_list_holds_one_row_per_candidate_from_the_later_run(
        self, client, isolated_db
    ):
        _, second = self._two_runs_on_one_date()
        recs = _get(client)["recommendations"]
        assert [r["candidate_id"] for r in recs] == [pb.FULL_IDS["retire"]]
        assert recs[0]["review_run_id"] == second["run_id"]
        assert recs[0]["explanation"] == "Explanation written by the second run."

    def test_rows_of_a_run_that_never_completed_and_rows_with_no_run_link_are_hidden(
        self, client, isolated_db
    ):
        record = pb.full_record(pb.RUN_ID_FIRST)
        candidate = pb.entry_of(record, pb.FULL_IDS["retire"])
        own = pb.recommendation_for(record, candidate)
        orphan = dict(own, candidate_id="orphan-cand", review_run_id=pb.RUN_ID_THIRD)
        unlinked = {k: v for k, v in own.items() if k != "review_run_id"}
        unlinked["candidate_id"] = "unlinked-cand"
        pb.store_recommendation(orphan)
        pb.store_recommendation(unlinked)
        pb.store_recommendation(own)
        pb.store_run(record)
        recs = _get(client)["recommendations"]
        assert [r["candidate_id"] for r in recs] == [pb.FULL_IDS["retire"]], (
            "only recommendation rows linked to the latest completed run may be listed"
        )


class TestStrictJsonThroughLists:
    def test_non_finite_numbers_inside_lists_are_served_as_null(self, client, isolated_db):
        record = pb.full_record()
        entry = pb.entry_of(record, pb.FULL_IDS["keep"])
        entry["verdict_reasons"] = ["finite reason", float("nan"), float("inf")]
        entry["evidence"]["marginal"]["excluded"] = [
            {"display_name": "Nested", "reason": float("-inf")}
        ]
        pb.store_run(record)
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "NaN" not in body and "Infinity" not in body
        served = _entries_by_id(pb.strict_json(resp))[pb.FULL_IDS["keep"]]
        assert served["verdict_reasons"] == ["finite reason", None, None]
        assert served["evidence"]["marginal"]["excluded"] == [
            {"display_name": "Nested", "reason": None}
        ]


class TestReviewStatusReportsTheLiveRunState:
    def test_a_run_in_progress_is_reported_with_who_started_it_and_when(self, client, isolated_db):
        pb.store_full_run()
        assert tenure_review.REVIEW_RUN_STATE.try_start("on_demand")
        data = _get(client)
        status = data["review_status"]
        assert status["running"] is True
        assert status["trigger"] == "on_demand"
        assert isinstance(status["running_since"], str) and status["running_since"]
        assert data["review_run"]["run_id"] == pb.RUN_ID_FIRST, (
            "the previous completed run stays the one served while a run is in progress"
        )

    def test_a_failed_attempt_is_reported_with_its_reason_and_time(self, client, isolated_db):
        pb.store_full_run()
        state = tenure_review.REVIEW_RUN_STATE
        assert state.try_start("nightly")
        state.finish_failure("Composer statistics unavailable", datetime(2026, 12, 31, tzinfo=UTC))
        data = _get(client)
        status = data["review_status"]
        assert status["running"] is False
        assert status["last_failed_attempt"]["reason"] == "Composer statistics unavailable"
        assert status["last_failed_attempt"]["at"].startswith("2026-12-31")
        assert data["review_run"]["run_id"] == pb.RUN_ID_FIRST


# ---------------------------------------------------------------------------
# No Composer request on a read path
# ---------------------------------------------------------------------------


class TestTheApiReadsOnlyTheStoredRun:
    def test_serving_a_completed_run_makes_no_outbound_request(
        self, client, isolated_db, monkeypatch
    ):
        record = pb.store_full_run()
        attempts = pb.forbid_network(monkeypatch)
        data = _get(client)
        assert data["review_run"]["run_id"] == pb.RUN_ID_FIRST
        assert len(data["review_entries"]) == len(record["entries"])
        assert attempts == []

    def test_serving_the_run_writes_nothing(self, client, isolated_db):
        pb.store_full_run()
        before = pb.observation_count()
        assert _get(client)["review_run"]["run_id"] == pb.RUN_ID_FIRST
        _get(client)
        assert pb.observation_count() == before
        assert db_module.get_retirement_decisions() == []


# ---------------------------------------------------------------------------
# Authentication on the review API
# ---------------------------------------------------------------------------

_XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture()
def read_spy(monkeypatch):
    """Record every call to the advisor observation reader, to prove a refused request
    read nothing."""
    calls = []
    real = db_module.get_advisor_observations_for_role

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(db_module, "get_advisor_observations_for_role", spy)
    return calls


class TestReviewApiRefusals:
    def test_an_unauthenticated_request_gets_401_and_nothing_is_read_or_written(
        self, client, isolated_db, monkeypatch, read_spy
    ):
        pb.store_full_run()
        # The same data is served while the gate is open, so the refusal below is the
        # gate's doing and not an empty store.
        assert _get(client)["review_run"]["run_id"] == pb.RUN_ID_FIRST
        read_spy.clear()
        before = pb.observation_count()

        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        with app_module.app.test_client() as locked:
            resp = locked.get("/api/retirement-recommendations")
        assert resp.status_code == 401
        assert resp.get_json() == {"error": "unauthenticated"}
        assert pb.FULL_NAMES["retire"] not in resp.get_data(as_text=True)
        assert read_spy == [], "a refused request must read nothing"
        assert pb.observation_count() == before

    @pytest.mark.parametrize(
        "unset",
        [
            ("SECRET_KEY", "FLASK_SECRET_KEY"),
            ("DASHBOARD_PASSWORD", "DASHBOARD_PASSWORD_HASH"),
        ],
        ids=["secret key not configured", "dashboard credential not configured"],
    )
    @pytest.mark.parametrize("headers", [{}, _XHR], ids=["api path", "api path with xhr header"])
    def test_unconfigured_authentication_answers_503_and_nothing_is_read_or_written(
        self, client, isolated_db, monkeypatch, read_spy, unset, headers
    ):
        pb.store_full_run()
        assert _get(client)["review_run"] is not None
        read_spy.clear()
        before = pb.observation_count()

        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
        for name in unset:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        with app_module.app.test_client() as locked:
            resp = locked.get("/api/retirement-recommendations", headers=headers)
        assert resp.status_code == 503
        assert resp.get_json() == {"error": "misconfigured"}
        assert pb.FULL_NAMES["retire"] not in resp.get_data(as_text=True)
        assert read_spy == []
        assert pb.observation_count() == before


class TestMalformedStoredData:
    def test_malformed_entries_never_break_the_response_and_good_entries_survive(
        self, client, isolated_db
    ):
        record = pb.full_record()
        good = pb.entry_of(record, pb.FULL_IDS["keep"])
        record["entries"] = [good, "garbage", 42, None, {"symphony_id": "partial-only"}]
        pb.store_run(record)
        data = _get(client)
        assert data["review_run"]["run_id"] == record["run_id"]
        assert pb.FULL_IDS["keep"] in _entries_by_id(data)

    def test_a_failed_read_of_the_run_alone_degrades_the_review_fields_and_never_500s(
        self, client, isolated_db, monkeypatch
    ):
        pb.store_full_run()
        real = db_module.get_advisor_observations_for_role

        def selective(role, *args, **kwargs):
            if role == tenure_review.TENURE_REVIEW_RUN_ROLE:
                raise RuntimeError("simulated run read failure")
            return real(role, *args, **kwargs)

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", selective)
        data = _get(client)
        assert data["review_run"] is None
        assert data["review_entries"] == []
        assert data["recommendations"] == [], (
            "the run's own recommendation rows cannot be told from an unfinished run's when "
            "the run cannot be read"
        )


# ---------------------------------------------------------------------------
# A run that cannot be read fails closed: which recommendation rows belong to it is
# unknown, so none are listed (the latest-date list can include the rows of a run that
# never finished, and it is only right when no run was ever stored).
# ---------------------------------------------------------------------------


def _seed_unrelated_recommendation_of_another_run(record) -> None:
    candidate = pb.entry_of(record, pb.FULL_IDS["retire"])
    body = pb.recommendation_for(record, candidate)
    pb.store_recommendation(
        dict(body, candidate_id="orphan-of-unfinished-run", review_run_id=pb.RUN_ID_THIRD)
    )


class TestFailClosedWhenTheLatestRunIsUnusable:
    def test_a_failed_read_of_the_run_lists_no_recommendation_of_an_unfinished_run(
        self, client, isolated_db, monkeypatch
    ):
        record = pb.store_full_run()
        _seed_unrelated_recommendation_of_another_run(record)
        real = db_module.get_advisor_observations_for_role

        def selective(role, *args, **kwargs):
            if role == tenure_review.TENURE_REVIEW_RUN_ROLE:
                raise RuntimeError("simulated run read failure")
            return real(role, *args, **kwargs)

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", selective)
        data = _get(client)
        assert data["recommendations"] == [], (
            "a list that falls back to the latest-date filter shows an unfinished run's rows"
        )
        assert data["review_run"] is None and data["review_entries"] == []

    @pytest.mark.parametrize(
        "stored",
        ['"plain text"', '["not", "a", "record"]', "42", "null", "this is not json {"],
        ids=["json text", "json list", "json number", "json null", "unparseable"],
    )
    def test_a_stored_record_that_is_not_a_dict_lists_no_recommendation(
        self, client, isolated_db, stored
    ):
        _seed_recommendation("legacy-cand", "legacy-sib")
        db_module.insert_advisor_observation(
            advisor_role=tenure_review.TENURE_REVIEW_RUN_ROLE,
            subject_type="tenure_review_run",
            subject_id=pb.RUN_ID_FIRST,
            verdict="completed",
            raw_response=stored,
        )
        data = _get(client)
        assert data["recommendations"] == []
        assert data["review_run"] is None and data["review_entries"] == []

    def test_a_stored_record_with_no_run_id_matches_no_recommendation(self, client, isolated_db):
        """A recommendation row written before reviews existed carries no run id at all, so
        a record whose run id is also missing must not be taken to own it."""
        _seed_recommendation("legacy-cand", "legacy-sib")
        db_module.insert_advisor_observation(
            advisor_role=tenure_review.TENURE_REVIEW_RUN_ROLE,
            subject_type="tenure_review_run",
            subject_id=pb.RUN_ID_FIRST,
            verdict="completed",
            raw_response={"trigger": "nightly", "entries": []},
        )
        assert _get(client)["recommendations"] == []

    def test_with_no_run_row_at_all_the_latest_date_list_still_applies(self, client, isolated_db):
        _seed_recommendation("legacy-cand", "legacy-sib")
        data = _get(client)
        assert [r["candidate_id"] for r in data["recommendations"]] == ["legacy-cand"]


# ---------------------------------------------------------------------------
# The shared review fetch never raises; any failure is an honest empty answer.
# ---------------------------------------------------------------------------


@pytest.fixture()
def review_module_unimportable(monkeypatch):
    """Make ``from advisors import tenure_review`` raise ImportError inside the app: the
    package attribute is removed (a from-import reads it first) and the module entry is
    set to None (which makes the fallback import fail)."""
    import advisors

    monkeypatch.delattr(advisors, "tenure_review")
    monkeypatch.setitem(sys.modules, "advisors.tenure_review", None)


class TestTheReviewFetchNeverRaises:
    def test_a_review_module_that_cannot_be_imported_still_answers_200(
        self, client, isolated_db, review_module_unimportable
    ):
        _seed_recommendation("legacy-cand", "legacy-sib")
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200, "an import failure became a server error"
        data = pb.strict_json(resp)
        assert data["review_run"] is None
        assert data["review_entries"] == []
        assert isinstance(data["review_status"], dict) and "running" in data["review_status"]
        assert data["recommendations"] == [], (
            "the run that owns the rows is unknown, so no row may be listed"
        )

    def test_a_run_state_snapshot_that_raises_still_answers_200(
        self, client, isolated_db, monkeypatch
    ):
        pb.store_full_run()

        def broken():
            raise RuntimeError("simulated run state failure")

        monkeypatch.setattr(tenure_review.REVIEW_RUN_STATE, "snapshot", broken)
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200, "a run state failure became a server error"
        data = pb.strict_json(resp)
        assert isinstance(data["review_status"], dict) and "running" in data["review_status"]


# ---------------------------------------------------------------------------
# One join of retirement decisions serves recommendations and review entries.
# ---------------------------------------------------------------------------


class TestOneDecisionJoin:
    def test_the_join_takes_the_id_key_and_the_absent_status_as_keyword_only_options(self):
        params = inspect.signature(app_module._join_retirement_approval_status).parameters
        assert params["id_key"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["id_key"].default == "candidate_id"
        assert params["absent_status"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["absent_status"].default == "pending"

    def test_one_function_stamps_entries_by_symphony_id_and_recommendations_by_candidate_id(
        self, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(
            db_module,
            "get_retirement_decisions",
            lambda: [
                {"candidate_id": "hash-a", "approval_status": "approved"},
                {"candidate_id": "hash-b", "approval_status": "pending"},
            ],
        )
        entries = [{"symphony_id": "hash-a"}, {"symphony_id": "hash-b"}, {"symphony_id": "hash-c"}]
        app_module._join_retirement_approval_status(
            entries, id_key="symphony_id", absent_status=None
        )
        assert [e["approval_status"] for e in entries] == ["approved", "pending", None]
        recs = [{"candidate_id": "hash-a"}, {"candidate_id": "hash-c"}]
        app_module._join_retirement_approval_status(recs)
        assert [r["approval_status"] for r in recs] == ["approved", "pending"], (
            "the call the recommendation list already makes must keep its meaning"
        )

    def test_a_failed_read_leaves_each_list_at_its_own_absent_status(
        self, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(
            db_module,
            "get_retirement_decisions",
            lambda: (_ for _ in ()).throw(RuntimeError("simulated decision read failure")),
        )
        entries = [{"symphony_id": "hash-a"}]
        app_module._join_retirement_approval_status(
            entries, id_key="symphony_id", absent_status=None
        )
        recs = [{"candidate_id": "hash-a"}]
        app_module._join_retirement_approval_status(recs)
        assert entries[0]["approval_status"] is None
        assert recs[0]["approval_status"] == "pending"

    def test_no_second_function_in_the_app_reads_the_decisions_table(self):
        source = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
        readers = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get_retirement_decisions"
                ):
                    readers.append(node.name)
                    break
        assert readers == ["_join_retirement_approval_status"], (
            f"the decisions table is read by {readers}; a second join can drift from the first"
        )


# ---------------------------------------------------------------------------
# The route and the fetch helper document what they now return and filter.
# ---------------------------------------------------------------------------


class TestDocstringsNameWhatChanged:
    @staticmethod
    def _flat(fn) -> str:
        return " ".join((fn.__doc__ or "").split())

    @pytest.mark.parametrize(
        "key", ["review_run", "review_entries", "review_status", "review_read_failed"]
    )
    def test_the_route_docstring_names_each_new_response_key(self, key):
        assert key in self._flat(app_module.api_retirement_recommendations)

    def test_the_route_docstring_states_that_the_list_follows_the_latest_run(self):
        doc = self._flat(app_module.api_retirement_recommendations)
        assert "review_run_id" in doc or "latest run" in doc.lower()

    def test_the_fetch_helper_docstring_names_the_run_id_filter(self):
        doc = self._flat(app_module._fetch_retirement_recommendations)
        assert "review_run_id" in doc


# ---------------------------------------------------------------------------
# review_read_failed: whether the empty review fields mean "nothing stored" or "the
# stored review cannot be read". Both leave review_run null and review_entries empty,
# so a client cannot tell them apart without this flag.
# ---------------------------------------------------------------------------


def _store_unreadable_run_record(raw_response) -> None:
    db_module.insert_advisor_observation(
        advisor_role=tenure_review.TENURE_REVIEW_RUN_ROLE,
        subject_type="tenure_review_run",
        subject_id=pb.RUN_ID_FIRST,
        verdict="completed",
        raw_response=raw_response,
    )


def _make_run_read_raise(monkeypatch) -> None:
    real = db_module.get_advisor_observations_for_role

    def selective(role, *args, **kwargs):
        if role == tenure_review.TENURE_REVIEW_RUN_ROLE:
            raise RuntimeError("simulated run read failure")
        return real(role, *args, **kwargs)

    monkeypatch.setattr(db_module, "get_advisor_observations_for_role", selective)


def _read_failed_flag(data):
    assert "review_read_failed" in data, "the response carries no review_read_failed key"
    return data["review_read_failed"]


class TestReviewReadFailedSaysWhetherTheStoredReviewCanBeRead:
    def test_a_completed_run_reports_false(self, client, isolated_db):
        pb.store_full_run()
        data = _get(client)
        assert _read_failed_flag(data) is False
        assert data["review_run"] is not None and data["review_entries"]

    def test_with_no_run_stored_it_reports_false_with_or_without_older_recommendations(
        self, client, isolated_db
    ):
        assert _read_failed_flag(_get(client)) is False
        _seed_recommendation("legacy-cand", "legacy-sib")
        data = _get(client)
        assert _read_failed_flag(data) is False, (
            "nothing stored is not a failure: the latest-date list applies"
        )
        assert [r["candidate_id"] for r in data["recommendations"]] == ["legacy-cand"]

    def test_a_completed_run_with_no_entries_reports_false(self, client, isolated_db):
        pb.store_run(pb.empty_record())
        data = _get(client)
        assert data["review_entries"] == []
        assert _read_failed_flag(data) is False, (
            "empty entries beside a header is an honest empty run, not a failed read"
        )

    def test_a_completed_run_that_lists_no_recommendation_reports_false(self, client, isolated_db):
        pb.store_run(pb.small_record())
        data = _get(client)
        assert data["recommendations"] == []
        assert _read_failed_flag(data) is False, (
            "an empty recommendation list does not mean the run could not be read"
        )

    def test_a_run_state_snapshot_that_raises_does_not_make_the_stored_run_unreadable(
        self, client, isolated_db, monkeypatch
    ):
        pb.store_full_run()

        def broken():
            raise RuntimeError("simulated run state failure")

        monkeypatch.setattr(tenure_review.REVIEW_RUN_STATE, "snapshot", broken)
        data = _get(client)
        assert _read_failed_flag(data) is False
        assert data["review_run"] is not None

    def test_a_failed_read_of_the_run_reports_true_with_the_review_fields_empty(
        self, client, isolated_db, monkeypatch
    ):
        record = pb.store_full_run()
        _seed_unrelated_recommendation_of_another_run(record)
        _make_run_read_raise(monkeypatch)
        data = _get(client)
        assert _read_failed_flag(data) is True
        assert data["review_run"] is None
        assert data["review_entries"] == []
        assert data["recommendations"] == []

    @pytest.mark.parametrize(
        "stored",
        ['"plain text"', '["not", "a", "record"]', "42", "null", "this is not json {"],
        ids=["json text", "json list", "json number", "json null", "unparseable"],
    )
    def test_a_stored_record_that_is_not_a_dict_reports_true_with_the_review_fields_empty(
        self, client, isolated_db, stored
    ):
        _seed_recommendation("legacy-cand", "legacy-sib")
        _store_unreadable_run_record(stored)
        data = _get(client)
        assert _read_failed_flag(data) is True
        assert data["review_run"] is None
        assert data["review_entries"] == []
        assert data["recommendations"] == []

    def test_a_stored_record_with_no_run_id_reports_true_as_the_other_fields_read_unusable(
        self, client, isolated_db
    ):
        """The record is a dict but names no run, so the header is withheld and no
        recommendation can be tied to it: the same state as the cases above, so the same flag."""
        _seed_recommendation("legacy-cand", "legacy-sib")
        _store_unreadable_run_record({"trigger": "nightly", "entries": []})
        data = _get(client)
        assert _read_failed_flag(data) is True
        assert data["review_run"] is None and data["recommendations"] == []

    def test_a_review_module_that_cannot_be_imported_reports_true(
        self, client, isolated_db, review_module_unimportable
    ):
        _seed_recommendation("legacy-cand", "legacy-sib")
        data = _get(client)
        assert _read_failed_flag(data) is True
        assert data["review_run"] is None and data["recommendations"] == []

    def test_the_newest_row_decides_when_a_broken_row_is_followed_by_a_good_run(
        self, client, isolated_db
    ):
        _store_unreadable_run_record('["not", "a", "record"]')
        pb.store_full_run(pb.RUN_ID_SECOND)
        data = _get(client)
        assert _read_failed_flag(data) is False
        assert data["review_run"]["run_id"] == pb.RUN_ID_SECOND

    def test_the_newest_row_decides_when_a_good_run_is_followed_by_a_broken_row(
        self, client, isolated_db
    ):
        pb.store_full_run(pb.RUN_ID_FIRST)
        _store_unreadable_run_record('["not", "a", "record"]')
        data = _get(client)
        assert _read_failed_flag(data) is True
        assert data["review_run"] is None and data["review_entries"] == []

    def test_the_flag_is_written_as_a_json_boolean_in_every_state(
        self, client, isolated_db, monkeypatch
    ):
        """The literal true or false in the body: not 1 or 0, not a string, not absent."""
        pattern = re.compile(r'"review_read_failed"\s*:\s*(true|false)\b')
        bodies = [client.get("/api/retirement-recommendations").get_data(as_text=True)]
        pb.store_full_run()
        bodies.append(client.get("/api/retirement-recommendations").get_data(as_text=True))
        _make_run_read_raise(monkeypatch)
        bodies.append(client.get("/api/retirement-recommendations").get_data(as_text=True))
        found = [pattern.search(body) for body in bodies]
        assert all(found), "the key is missing or is not a JSON boolean in some state"
        assert [m.group(1) for m in found] == ["false", "false", "true"]


class TestTheCompositeItemServesItsPopulationSize:
    def test_every_available_composite_item_carries_its_stored_whole_number_population(
        self, client, isolated_db
    ):
        record = pb.store_full_run()
        got = _entries_by_id(_get(client))
        checked = 0
        for stored in record["entries"]:
            item = stored["evidence"]["composite"]
            if item["status"] != "available":
                continue
            served = got[stored["symphony_id"]]["evidence"]["composite"]
            assert isinstance(served["population_size"], int), stored["display_name"]
            assert not isinstance(served["population_size"], bool), stored["display_name"]
            assert served["population_size"] == item["population_size"], stored["display_name"]
            checked += 1
        assert checked >= 3


class TestTheRedundancyItemServesWhatThePanelRenders:
    """The panel renders the list of peers not compared; the API serves the stored item
    as it is, so the two surfaces list the same peers with the same reasons."""

    @pytest.mark.parametrize("which", ["full", "small", "edge", "quiet"])
    def test_the_served_item_carries_the_stored_not_compared_list_and_the_stored_missing_items(
        self, client, isolated_db, which
    ):
        builders = {
            "full": pb.full_record,
            "small": pb.small_record,
            "edge": pb.edge_record,
            "quiet": pb.quiet_record,
        }
        record = builders[which]()
        pb.store_run(record)
        got = _entries_by_id(_get(client))
        listed = 0
        for stored in record["entries"]:
            served = got[stored["symphony_id"]]
            item = stored["evidence"]["redundancy"]
            assert served["evidence"]["redundancy"].get("not_compared") == item.get(
                "not_compared"
            ), stored["display_name"]
            assert served["missing_items"] == stored["missing_items"], stored["display_name"]
            listed += len(item.get("not_compared") or [])
        assert listed >= (3 if which == "full" else 0)


class TestNoEntryListsItselfAsLeftOut:
    @pytest.mark.parametrize("which", ["full", "small", "edge"])
    def test_an_entrys_comparison_item_never_names_the_entry_itself(
        self, client, isolated_db, which
    ):
        builders = {"full": pb.full_record, "small": pb.small_record, "edge": pb.edge_record}
        pb.store_run(builders[which]())
        served = _get(client)["review_entries"]
        assert served, "fixture sanity: the record has entries"
        listed = 0
        for entry in served:
            excluded = entry["evidence"]["marginal"].get("excluded") or []
            names = [x["display_name"] for x in excluded]
            assert entry["display_name"] not in names, entry["display_name"]
            listed += len(names)
        if which != "edge":
            assert listed, "fixture sanity: some entry lists a left-out symphony"
