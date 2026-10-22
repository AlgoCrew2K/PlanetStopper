"""RED tests -- Retirement run summary: tick persistence + read path
(Cycle 2e AC-4, AC-5; feature-plans/retirement-visibility.md).

AC-4: ``app._retirement_recommender_tick_worker`` calls
``advisors.retirement_recommender.build_recommendations_with_summary(
source="composer")`` -> ``(recs, summary)``; stamps ``summary["run_id"]`` onto
every rec; persists the recs (``persist_recommendations``); THEN ALWAYS
persists exactly one ``RETIREMENT_RUN_SUMMARY`` advisor_observations row whose
``recommendations`` field is the number of rec rows actually persisted. That
role is NOT in ``_ADVISOR_ROLES``. ``run_id`` is excluded from
``_canonical_evidence_snapshot`` (so a stable pair still reuses its prior
explanation -- no new nightly LLM spend).

AC-5: ``_fetch_retirement_last_run()`` returns the latest
RETIREMENT_RUN_SUMMARY ``raw_response`` or ``None`` (raising on DB failure --
callers degrade). ``_fetch_retirement_recommendations`` returns only the recs
whose ``run_id`` equals the latest summary's ``run_id`` when one exists, and
falls back to today's latest-calendar-date filter when no summary exists or
the latest summary carries no ``run_id`` (legacy rows).
``GET /api/retirement-recommendations`` gains an additive ``last_run`` key.

Only the network-adjacent seams are mocked: ``build_recommendations_with_summary``
(its own behaviour is covered in tests/advisors/
test_retirement_recommender_composer_source.py) and
``explain_recommendation`` (the LLM). The DB, ``persist_recommendations`` and
the app helpers under test are the REAL production code.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

import app as app_module
import database as db_module
from tests.app._retirement_run_summary_stub import make_summary, with_summary

_SUMMARY_ROLE = "RETIREMENT_RUN_SUMMARY"
_REC_ROLE = "RETIREMENT_RECOMMENDATION"

_CAND = "cand-run-aaa"
_SIB = "sib-run-bbb"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_run_summary.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    db_module.run_migrations()
    return db_path


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _rec(candidate_id=_CAND, sibling_id=_SIB, **overrides) -> dict:
    """A recommendation dict shaped like build_recommendations' real output
    (+ the Cycle-2c names, so the reuse snapshot is complete)."""
    rec = {
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
            "sharpe": 0.20,
            "sortino": 0.25,
            "max_drawdown": -0.30,
            "calmar": 0.10,
        },
        "sibling_metrics": {
            "annualized_return": 0.18,
            "sharpe": 1.40,
            "sortino": 1.80,
            "max_drawdown": -0.06,
            "calmar": 3.00,
        },
        "uncertainty_gate_passed": True,
        "structural_redundancy_gate_passed": True,
        "stressed_correlation": 0.78,
        "holdings_overlap": 0.10,
        "basis_label": "realized daily returns (Composer, deposit-adjusted)",
        "candidate_name": "Candidate Name",
        "sibling_name": "Sibling Name",
    }
    rec.update(overrides)
    return rec


def _seed_roster(id_to_name: dict[str, str]) -> None:
    db_module.save_state({sid: {"name": n, "logic_holdings": {}} for sid, n in id_to_name.items()})


def _rows(role: str) -> list[dict]:
    """Newest-first rows for `role` (the production read accessor)."""
    return db_module.get_advisor_observations_for_role(role, limit=500)


def _set_created_at(row_id: int, ts: str) -> None:
    conn = db_module.get_connection()
    conn.execute("UPDATE advisor_observations SET created_at = ? WHERE id = ?", (ts, row_id))
    conn.commit()
    conn.close()


def _insert_summary(**overrides) -> tuple[int, dict]:
    summary = make_summary(**overrides)
    row_id = db_module.insert_advisor_observation(
        advisor_role=_SUMMARY_ROLE,
        subject_type="fleet",
        subject_id="retirement",
        verdict=summary["status"],
        raw_response=summary,
    )
    return row_id, summary


def _insert_rec(run_id: str | None, candidate_id=_CAND, sibling_id=_SIB, **overrides) -> int:
    raw = _rec(candidate_id, sibling_id, **overrides)
    if run_id is not None:
        raw["run_id"] = run_id
    return db_module.insert_advisor_observation(
        advisor_role=_REC_ROLE,
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=raw,
    )


class _Producer:
    """Installs fake build_recommendations_with_summary + explain_recommendation
    on the REAL modules (the worker imports both lazily at call time)."""

    def __init__(self, monkeypatch, recs, summary=None, explanation="explanation text"):
        import advisors.retirement_explainer as explainer_module  # noqa: PLC0415
        import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

        self.build_calls: list[dict] = []
        self.explain_calls: list[dict] = []
        self.summary = summary
        self._recs = recs

        def _build(**kwargs):
            self.build_calls.append(dict(kwargs))
            produced = [dict(r) for r in self._recs]  # a fresh recompute each call
            sm = self.summary if self.summary is not None else make_summary()
            return produced, dict(sm)

        def _explain(rec):
            self.explain_calls.append(dict(rec))
            return explanation

        monkeypatch.setattr(recommender_module, "build_recommendations_with_summary", _build)
        monkeypatch.setattr(explainer_module, "explain_recommendation", _explain)


def _run_worker():
    app_module._retirement_recommender_tick_worker()


# ===========================================================================
# AC-4: the tick persists the run summary
# ===========================================================================


class TestTickCallsTheComposerSource:
    def test_worker_requests_the_composer_source_and_nothing_else(self, isolated_db, monkeypatch):
        producer = _Producer(monkeypatch, [])
        _run_worker()
        assert producer.build_calls == [{"source": "composer"}]


class TestTickAlwaysPersistsExactlyOneSummaryRow:
    def test_zero_recommendations_still_persist_one_summary_row(self, isolated_db, monkeypatch):
        summary = make_summary(status="ok", recommendations=0)
        _Producer(monkeypatch, [], summary=summary)
        _run_worker()

        rows = _rows(_SUMMARY_ROLE)
        assert len(rows) == 1
        assert rows[0]["raw_response"]["run_id"] == summary["run_id"]
        assert rows[0]["raw_response"]["recommendations"] == 0
        assert _rows(_REC_ROLE) == []

    def test_with_recommendations_one_summary_and_n_recommendation_rows(
        self, isolated_db, monkeypatch
    ):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name", "c2": "C2", "s2": "S2"})
        recs = [_rec(), _rec("c2", "s2")]
        summary = make_summary(recommendations=2)
        _Producer(monkeypatch, recs, summary=summary)
        _run_worker()

        assert len(_rows(_REC_ROLE)) == 2
        summary_rows = _rows(_SUMMARY_ROLE)
        assert len(summary_rows) == 1
        assert summary_rows[0]["raw_response"]["run_id"] == summary["run_id"]

    def test_summary_row_preserves_the_producers_summary_fields(self, isolated_db, monkeypatch):
        summary = make_summary(
            status="series_unavailable",
            roster_size=5,
            symphonies_with_series=1,
            symphonies_without_series=[{"symphony_id": "x", "reason": "no_account"}],
            pairs_screened=0,
            stress_gate_min_aligned_days=173,
        )
        _Producer(monkeypatch, [], summary=summary)
        _run_worker()

        persisted = _rows(_SUMMARY_ROLE)[0]["raw_response"]
        for key in (
            "run_id",
            "run_at",
            "source",
            "basis_label",
            "status",
            "roster_size",
            "symphonies_with_series",
            "symphonies_without_series",
            "stress_gate_min_aligned_days",
            "screen_threshold",
        ):
            assert persisted[key] == summary[key], key

    def test_internal_error_summary_is_persisted_with_zero_recommendations(
        self, isolated_db, monkeypatch
    ):
        summary = make_summary(status="internal_error", error_type="ZeroDivisionError")
        _Producer(monkeypatch, [], summary=summary)
        _run_worker()

        (row,) = _rows(_SUMMARY_ROLE)
        assert row["raw_response"]["status"] == "internal_error"
        assert row["raw_response"]["error_type"] == "ZeroDivisionError"
        assert row["raw_response"]["recommendations"] == 0

    def test_summary_recommendations_is_the_persisted_row_count_not_the_producers_claim(
        self, isolated_db, monkeypatch
    ):
        """The producer's summary claims 7; only 2 rows are actually persisted."""
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name", "c2": "C2", "s2": "S2"})
        _Producer(
            monkeypatch,
            [_rec(), _rec("c2", "s2")],
            summary=make_summary(recommendations=7),
        )
        _run_worker()

        persisted_count = len(_rows(_REC_ROLE))
        assert persisted_count == 2
        assert _rows(_SUMMARY_ROLE)[0]["raw_response"]["recommendations"] == persisted_count

    def test_a_short_persist_count_is_what_the_summary_reports(self, isolated_db, monkeypatch):
        """persist_recommendations is the authority on 'actually persisted': if it
        reports 1 for a batch of 2, the summary says 1."""
        import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

        _Producer(monkeypatch, [_rec(), _rec("c2", "s2")])
        monkeypatch.setattr(recommender_module, "persist_recommendations", lambda recs, **kw: 1)
        _run_worker()

        assert _rows(_SUMMARY_ROLE)[0]["raw_response"]["recommendations"] == 1

    def test_back_to_back_runs_each_persist_their_own_summary(self, isolated_db, monkeypatch):
        producer = _Producer(monkeypatch, [])
        _run_worker()
        _run_worker()
        rows = _rows(_SUMMARY_ROLE)
        assert len(rows) == 2
        assert rows[0]["raw_response"]["run_id"] != rows[1]["raw_response"]["run_id"]
        assert len(producer.build_calls) == 2


class TestRunIdStampingAndOrdering:
    def test_every_persisted_recommendation_carries_the_summarys_run_id(
        self, isolated_db, monkeypatch
    ):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name", "c2": "C2", "s2": "S2"})
        summary = make_summary()
        _Producer(monkeypatch, [_rec(), _rec("c2", "s2")], summary=summary)
        _run_worker()

        rec_rows = _rows(_REC_ROLE)
        assert len(rec_rows) == 2
        assert {r["raw_response"].get("run_id") for r in rec_rows} == {summary["run_id"]}

    def test_recommendation_rows_are_persisted_before_the_summary_row(
        self, isolated_db, monkeypatch
    ):
        """The Run-now poll keys off the summary's run_id; if the summary landed
        first, the page could reload with a new run_id and no cards yet."""
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name", "c2": "C2", "s2": "S2"})
        _Producer(monkeypatch, [_rec(), _rec("c2", "s2")])
        _run_worker()

        rec_ids = [r["id"] for r in _rows(_REC_ROLE)]
        (summary_row,) = _rows(_SUMMARY_ROLE)
        assert rec_ids
        assert max(rec_ids) < summary_row["id"]

    def test_summary_row_is_advisory_only(self, isolated_db, monkeypatch):
        _Producer(monkeypatch, [])
        _run_worker()
        (row,) = _rows(_SUMMARY_ROLE)
        assert row["advisor_role"] == _SUMMARY_ROLE
        assert row["is_advisory_only"] == 1

    def test_the_summary_role_is_not_an_overview_advisor_role(self):
        assert _SUMMARY_ROLE not in app_module._ADVISOR_ROLES
        assert _REC_ROLE not in app_module._ADVISOR_ROLES  # same convention (control)


class TestTickFailureIsolation:
    def test_a_producer_exception_is_survived_and_recorded_as_an_internal_error_summary(
        self, isolated_db, monkeypatch, caplog
    ):
        import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

        def _boom(**kwargs):
            raise RuntimeError("secret-producer-detail-9999")

        monkeypatch.setattr(recommender_module, "build_recommendations_with_summary", _boom)
        _run_worker()  # must not raise

        (row,) = _rows(_SUMMARY_ROLE)
        raw = row["raw_response"]
        assert raw["status"] == "internal_error"
        assert raw["error_type"] == "RuntimeError"
        assert raw["recommendations"] == 0
        assert re.fullmatch(r"[0-9a-f]{32}", raw["run_id"])
        assert raw.get("run_at")
        assert "secret-producer-detail-9999" not in json.dumps(raw), "D-1: type name only"
        assert "secret-producer-detail-9999" not in caplog.text
        assert _rows(_REC_ROLE) == []

    def test_a_recommendation_persist_failure_still_writes_a_zero_count_summary(
        self, isolated_db, monkeypatch
    ):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _Producer(monkeypatch, [_rec()])
        real_insert = db_module.insert_advisor_observation

        def _flaky_insert(**kwargs):
            if kwargs.get("advisor_role") == _REC_ROLE:
                raise RuntimeError("disk full")
            return real_insert(**kwargs)

        monkeypatch.setattr(db_module, "insert_advisor_observation", _flaky_insert)
        _run_worker()  # must not raise

        assert _rows(_REC_ROLE) == []
        (row,) = _rows(_SUMMARY_ROLE)
        assert row["raw_response"]["recommendations"] == 0

    def test_a_summary_persist_failure_never_propagates_and_recs_still_persist(
        self, isolated_db, monkeypatch
    ):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _Producer(monkeypatch, [_rec()])
        real_insert = db_module.insert_advisor_observation

        def _flaky_insert(**kwargs):
            if kwargs.get("advisor_role") == _SUMMARY_ROLE:
                raise RuntimeError("disk full")
            return real_insert(**kwargs)

        monkeypatch.setattr(db_module, "insert_advisor_observation", _flaky_insert)
        _run_worker()  # must not raise

        assert len(_rows(_REC_ROLE)) == 1
        assert _rows(_SUMMARY_ROLE) == []

    @pytest.mark.parametrize(
        "seam", ["_round_floats", "_retirement_find_reusable_prior_explanation"]
    )
    def test_an_exception_between_a_good_build_and_persist_still_yields_one_internal_error_row(
        self, isolated_db, monkeypatch, caplog, seam
    ):
        """AC-4 'ALWAYS exactly one summary row': the producer succeeded (status
        ok, 1 rec) but the worker blows up in its enrichment pass BEFORE persist.
        The run must not vanish (the Run-now UI would poll for 3 minutes on a run
        that died silently) and must not read 'ok'."""
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _Producer(monkeypatch, [_rec()], summary=make_summary(status="ok", recommendations=1))

        def _boom(*args, **kwargs):
            raise RuntimeError("secret-between-4321")

        monkeypatch.setattr(app_module, seam, _boom)
        _run_worker()  # must not raise

        assert _rows(_REC_ROLE) == [], "persist was never reached"
        (row,) = _rows(_SUMMARY_ROLE)
        raw = row["raw_response"]
        assert raw["status"] == "internal_error"
        assert raw["error_type"] == "RuntimeError"
        assert raw["recommendations"] == 0
        assert raw["run_id"], "the run keeps an id the UI can see change"
        assert "secret-between-4321" not in json.dumps(raw), "D-1: type name only"
        assert "secret-between-4321" not in caplog.text

    def test_an_explainer_failure_does_not_stop_the_summary(self, isolated_db, monkeypatch):
        import advisors.retirement_explainer as explainer_module  # noqa: PLC0415

        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _Producer(monkeypatch, [_rec()])

        def _explode(rec):
            raise RuntimeError("llm down")

        monkeypatch.setattr(explainer_module, "explain_recommendation", _explode)
        _run_worker()

        assert len(_rows(_REC_ROLE)) == 1
        assert len(_rows(_SUMMARY_ROLE)) == 1


# ===========================================================================
# AC-4: run_id never defeats explanation reuse
# ===========================================================================


class TestRunIdExcludedFromReuseSnapshot:
    def test_snapshot_ignores_run_id(self):
        a = _rec()
        a["run_id"] = "run-aaaa"
        b = _rec()
        b["run_id"] = "run-bbbb"
        c = _rec()  # no run_id at all (a pre-Cycle-2e row)

        snap_a = app_module._canonical_evidence_snapshot(a)
        assert "run_id" not in snap_a
        assert snap_a == app_module._canonical_evidence_snapshot(b)
        assert snap_a == app_module._canonical_evidence_snapshot(c)

    def test_snapshot_still_detects_a_real_evidence_change(self):
        a = _rec()
        a["run_id"] = "run-aaaa"
        b = _rec(correlation=0.55)
        b["run_id"] = "run-aaaa"
        assert app_module._canonical_evidence_snapshot(
            a
        ) != app_module._canonical_evidence_snapshot(b)

    def test_a_stable_pair_reuses_its_explanation_across_nights_with_distinct_run_ids(
        self, isolated_db, monkeypatch
    ):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        import advisors.retirement_explainer as explainer_module  # noqa: PLC0415
        import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

        explain_count = {"n": 0}

        def _explain(rec):
            explain_count["n"] += 1
            return f"explanation #{explain_count['n']}"

        # with_summary() mints a FRESH run_id on every build call, like the real producer.
        monkeypatch.setattr(
            recommender_module,
            "build_recommendations_with_summary",
            with_summary(lambda **kw: [_rec()]),
        )
        monkeypatch.setattr(explainer_module, "explain_recommendation", _explain)

        _run_worker()  # night 1
        _run_worker()  # night 2
        _run_worker()  # night 3

        assert explain_count["n"] == 1, "run_id differs nightly but evidence is unchanged"
        rec_rows = _rows(_REC_ROLE)
        assert len(rec_rows) == 3
        run_ids = {r["raw_response"]["run_id"] for r in rec_rows}
        assert len(run_ids) == 3, "each night's rows carry that night's run_id"
        assert {r["raw_response"]["explanation"] for r in rec_rows} == {"explanation #1"}


# ===========================================================================
# AC-5: _fetch_retirement_last_run
# ===========================================================================


class TestFetchRetirementLastRun:
    def test_returns_none_when_no_run_has_been_recorded(self, isolated_db):
        assert app_module._fetch_retirement_last_run() is None

    def test_returns_the_latest_summarys_raw_response(self, isolated_db):
        _insert_summary(status="ok", roster_size=3)
        _id2, newest = _insert_summary(status="series_unavailable", roster_size=9)

        got = app_module._fetch_retirement_last_run()
        assert isinstance(got, dict)
        assert got["run_id"] == newest["run_id"]
        assert got["status"] == "series_unavailable"
        assert got["roster_size"] == 9

    def test_other_advisor_roles_are_never_returned(self, isolated_db):
        _insert_rec("some-run")
        db_module.insert_advisor_observation(
            advisor_role="MARKET_PRISM",
            subject_type="market",
            subject_id="x",
            raw_response={"run_id": "not-a-retirement-run"},
        )
        assert app_module._fetch_retirement_last_run() is None

    def test_a_database_failure_raises_so_callers_can_degrade(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "DB_FILE", str(tmp_path / "no_such_dir" / "state.db"))
        # sqlite3.Error specifically: a missing helper (AttributeError) must NOT satisfy this.
        with pytest.raises(sqlite3.Error):
            app_module._fetch_retirement_last_run()


# ===========================================================================
# AC-5: run-scoped recommendation read
# ===========================================================================


class TestRunScopedRecommendationFilter:
    def _roster(self):
        _seed_roster(
            {
                "cand-old": "Old Candidate",
                "sib-old": "Old Sibling",
                "cand-new": "New Candidate",
                "sib-new": "New Sibling",
            }
        )

    def test_only_the_latest_runs_recommendations_are_returned(self, isolated_db):
        self._roster()
        _insert_rec("run-old", "cand-old", "sib-old")
        _insert_rec("run-new", "cand-new", "sib-new")
        _insert_summary(run_id="run-old", recommendations=1)
        _insert_summary(run_id="run-new", recommendations=1)

        recs = app_module._fetch_retirement_recommendations()
        assert [r["candidate_id"] for r in recs] == ["cand-new"]

    def test_a_zero_recommendation_latest_run_hides_the_earlier_runs_cards(self, isolated_db):
        """A 14:00 'Run now' that finds nothing must not leave the 03:45 run's
        cards on the panel (even though both are the same calendar date)."""
        self._roster()
        _insert_rec("run-morning", "cand-old", "sib-old")
        _insert_summary(run_id="run-morning", recommendations=1)
        _insert_summary(run_id="run-afternoon", recommendations=0)

        assert app_module._fetch_retirement_recommendations() == []

    def test_multiple_recommendations_of_the_latest_run_are_all_returned(self, isolated_db):
        self._roster()
        _insert_rec("run-new", "cand-old", "sib-old")
        _insert_rec("run-new", "cand-new", "sib-new")
        _insert_summary(run_id="run-new", recommendations=2)

        recs = app_module._fetch_retirement_recommendations()
        assert sorted(r["candidate_id"] for r in recs) == ["cand-new", "cand-old"]

    def test_without_any_summary_the_latest_calendar_date_filter_still_applies(self, isolated_db):
        self._roster()
        old_id = _insert_rec(None, "cand-old", "sib-old")
        new_id = _insert_rec(None, "cand-new", "sib-new")
        _set_created_at(old_id, "2026-10-19 03:45:00")
        _set_created_at(new_id, "2026-10-20 03:45:00")

        recs = app_module._fetch_retirement_recommendations()
        assert [r["candidate_id"] for r in recs] == ["cand-new"]

    def test_a_latest_summary_without_a_run_id_falls_back_to_the_calendar_date_filter(
        self, isolated_db
    ):
        self._roster()
        old_id = _insert_rec(None, "cand-old", "sib-old")
        new_id = _insert_rec(None, "cand-new", "sib-new")
        _set_created_at(old_id, "2026-10-19 03:45:00")
        _set_created_at(new_id, "2026-10-20 03:45:00")
        summary_id, _ = _insert_summary()
        # Strip the run_id to model a legacy/hand-written summary row.
        conn = db_module.get_connection()
        conn.execute(
            "UPDATE advisor_observations SET raw_response = ? WHERE id = ?",
            (json.dumps({"status": "ok", "recommendations": 0}), summary_id),
        )
        conn.commit()
        conn.close()

        recs = app_module._fetch_retirement_recommendations()
        assert [r["candidate_id"] for r in recs] == ["cand-new"]

    def test_run_scoped_rows_keep_the_request_time_overlays(self, isolated_db):
        self._roster()
        _insert_rec("run-new", "cand-new", "sib-new")
        _insert_summary(run_id="run-new", recommendations=1)

        (rec,) = app_module._fetch_retirement_recommendations()
        assert rec["approval_status"] == "pending"
        assert rec["candidate_name"] == "New Candidate"
        assert rec["sibling_name"] == "New Sibling"


# ===========================================================================
# AC-5: GET /api/retirement-recommendations carries last_run
# ===========================================================================


class TestApiLastRun:
    def test_last_run_is_null_when_no_run_exists(self, client, isolated_db):
        data = client.get("/api/retirement-recommendations").get_json()
        assert "last_run" in data
        assert data["last_run"] is None
        assert data["recommendations"] == []

    def test_last_run_carries_the_latest_summary(self, client, isolated_db):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _insert_rec("run-1", _CAND, _SIB)
        _id, summary = _insert_summary(
            run_id="run-1",
            status="ok",
            recommendations=1,
            symphonies_without_series=[{"symphony_id": "h1", "reason": "no_account"}],
        )

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        last_run = data["last_run"]
        assert last_run["run_id"] == "run-1"
        assert last_run["status"] == summary["status"]
        assert last_run["symphonies_without_series"] == summary["symphonies_without_series"]
        assert last_run["stress_gate_min_aligned_days"] == summary["stress_gate_min_aligned_days"]

    def test_recommendations_shape_is_unchanged_and_run_scoped(self, client, isolated_db):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _insert_rec("run-1", _CAND, _SIB)
        _insert_summary(run_id="run-1", recommendations=1)

        data = client.get("/api/retirement-recommendations").get_json()
        assert isinstance(data["recommendations"], list)
        (rec,) = data["recommendations"]
        for key in (
            "candidate_id",
            "sibling_id",
            "correlation",
            "basis_label",
            "approval_status",
            "candidate_name",
            "sibling_name",
        ):
            assert key in rec, key
        assert set(data) == {"recommendations", "last_run"}

    def test_a_latest_zero_rec_run_empties_recommendations_but_keeps_last_run(
        self, client, isolated_db
    ):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _insert_rec("run-morning", _CAND, _SIB)
        _insert_summary(run_id="run-morning", recommendations=1)
        _insert_summary(run_id="run-afternoon", recommendations=0)

        data = client.get("/api/retirement-recommendations").get_json()
        assert data["recommendations"] == []
        assert data["last_run"]["run_id"] == "run-afternoon"

    def test_the_workers_minimal_fallback_summary_is_served_by_the_api(self, client, isolated_db):
        """The 5-key fallback row the worker writes when the producer died (no
        roster_size, no symphonies_without_series, ...) must round-trip through
        the API untouched -- never a 500, never missing-key crashes."""
        fallback = {
            "run_id": "run-fallback",
            "run_at": "2026-10-22T03:45:00Z",
            "status": "internal_error",
            "error_type": "RuntimeError",
            "recommendations": 0,
        }
        db_module.insert_advisor_observation(
            advisor_role=_SUMMARY_ROLE,
            subject_type="fleet",
            subject_id="retirement",
            verdict="internal_error",
            raw_response=fallback,
        )

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["last_run"] == fallback
        assert data["recommendations"] == []

    def test_a_producer_failure_run_is_visible_through_the_api_end_to_end(
        self, client, isolated_db, monkeypatch
    ):
        import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

        def _boom(**kwargs):
            raise RuntimeError("producer down")

        monkeypatch.setattr(recommender_module, "build_recommendations_with_summary", _boom)
        _run_worker()

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["last_run"]["status"] == "internal_error"
        assert data["last_run"]["error_type"] == "RuntimeError"
        assert data["recommendations"] == []

    def test_a_last_run_read_failure_degrades_to_null_without_a_500(
        self, client, isolated_db, monkeypatch
    ):
        _seed_roster({_CAND: "Candidate Name", _SIB: "Sibling Name"})
        _insert_rec(None, _CAND, _SIB)

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(app_module, "_fetch_retirement_last_run", _boom)
        resp = client.get("/api/retirement-recommendations")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["last_run"] is None
        assert len(data["recommendations"]) == 1, "recs still served from the legacy path"

    def test_non_finite_numbers_in_the_summary_do_not_corrupt_the_json(self, client, isolated_db):
        _insert_summary(run_id="run-nan", recommendations=0)
        conn = db_module.get_connection()
        # json.dumps writes NaN as the bare token NaN -- exactly what a careless producer could store.
        conn.execute(
            "UPDATE advisor_observations SET raw_response = ? WHERE advisor_role = ?",
            (
                json.dumps(
                    {
                        "run_id": "run-nan",
                        "status": "ok",
                        "screen_threshold": float("nan"),
                        "symphonies_without_series": [
                            {"symphony_id": "x", "reason": "r", "ratio": float("inf")}
                        ],
                        "nested": {"deep": {"v": float("-inf")}},
                    }
                ),
                _SUMMARY_ROLE,
            ),
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        # A strict JSON parser rejects NaN/Infinity tokens (what a browser's response.json() does).
        parsed = json.loads(
            resp.get_data(as_text=True),
            parse_constant=lambda tok: pytest.fail(f"non-strict JSON token {tok} in response"),
        )
        assert parsed["last_run"]["run_id"] == "run-nan"
        assert parsed["last_run"]["screen_threshold"] is None, "NaN must be sanitised to null"
        entry = parsed["last_run"]["symphonies_without_series"][0]
        assert entry["ratio"] is None, "Infinity inside a list-of-dicts must be sanitised too"
        assert entry["reason"] == "r"
        assert parsed["last_run"]["nested"]["deep"]["v"] is None
