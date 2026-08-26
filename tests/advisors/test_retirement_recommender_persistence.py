"""RED tests -- Retirement Recommender persistence (AC-8).

Module under test: advisors.retirement_recommender (NEW).

THE CROSS-IMPLEMENTER CONTRACT (PM mandate, this cycle): the raw_response
evidence-dict's literal key names are defined ONCE here and reused IDENTICALLY
in tests/app/test_retirement_recommendations_route.py and
tests/app/test_retirement_recommendations_panel.py -- also written verbatim
into .claude/tdd-handoff.md as "the raw_response schema, authoritative".
retire-math produces these keys, retire-route renders them, retire-doc
documents them. Nobody invents a divergent name.

raw_response schema (authoritative):
    candidate_id: str                  -- the retirement candidate's symphony_id
    sibling_id: str                    -- the paired (kept) sibling's symphony_id
    correlation: float                 -- full-window Pearson r
    ci_lower: float                    -- Fisher-z 95% CI lower bound
    ci_upper: float                    -- Fisher-z 95% CI upper bound
    n_obs: int                         -- overlapping observations used
    candidate_composite: float         -- candidate's fleet-normalized composite score
    sibling_composite: float           -- sibling's fleet-normalized composite score
    candidate_metrics: dict            -- {annualized_return, sharpe, sortino, max_drawdown, calmar}
    sibling_metrics: dict              -- same 5-key shape, for the sibling
    uncertainty_gate_passed: bool
    structural_redundancy_gate_passed: bool
    stressed_correlation: float | None -- None when too-thin/undefined
    holdings_overlap: float | None     -- None when unavailable (off-hours)
    basis_label: str
"""

from __future__ import annotations

import math

import pytest

import database as db_module
from tests.advisors._retirement_recommender_reference import seed_state_db, trading_days

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
_METRICS_SUBKEYS = {"annualized_return", "sharpe", "sortino", "max_drawdown", "calmar"}


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as _rr  # noqa: PLC0415

    return _rr


def _build_screen_hit_db(tmp_path, monkeypatch):
    n = 200
    days = trading_days(n)
    # Linear ramp, not a sine wave -- review-response cycle 3 F3 fallout: see
    # test_retirement_recommender_composite.py's _fleet_scope_series docstring
    # for the full derivation (a downside-selection stress window can land in
    # a smooth function's near-zero-derivative extremum, collapsing the
    # sampled correlation under fixed-magnitude noise).
    a = [-0.10 + 0.20 * i / (n - 1) for i in range(n)]
    b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(a)]
    series = {
        "candidate-sym": {d: (a[i], 0.0) for i, d in enumerate(days)},
        "sibling-sym": {d: (b[i], 0.0) for i, d in enumerate(days)},
    }
    return seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)


class TestRawResponseSchema:
    def test_raw_response_has_exactly_the_authoritative_keys(self, rr, tmp_path, monkeypatch):
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert len(recs) >= 1, "fixture sanity: expected at least one recommendation"

        captured = []
        monkeypatch.setattr(
            db_module,
            "insert_advisor_observation",
            lambda **kwargs: captured.append(kwargs) or 1,
        )
        rr.persist_recommendations(recs, db_file=db_file)

        assert len(captured) == len(recs)
        for call_kwargs in captured:
            raw = call_kwargs.get("raw_response")
            assert isinstance(raw, dict), f"raw_response must be a dict, got {type(raw)!r}"
            missing = _RAW_RESPONSE_KEYS - raw.keys()
            assert not missing, f"raw_response is missing required keys: {missing}"

    def test_candidate_and_sibling_metrics_have_the_five_subkeys(self, rr, tmp_path, monkeypatch):
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        recs = rr.build_recommendations(db_file=db_file, days=None)

        captured = []
        monkeypatch.setattr(
            db_module,
            "insert_advisor_observation",
            lambda **kwargs: captured.append(kwargs) or 1,
        )
        rr.persist_recommendations(recs, db_file=db_file)

        for call_kwargs in captured:
            raw = call_kwargs["raw_response"]
            for side in ("candidate_metrics", "sibling_metrics"):
                assert set(raw[side].keys()) >= _METRICS_SUBKEYS, (
                    f"raw_response[{side!r}] is missing metric subkeys: "
                    f"{_METRICS_SUBKEYS - set(raw[side].keys())}"
                )

    def test_candidate_id_and_sibling_id_are_distinct(self, rr, tmp_path, monkeypatch):
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        for rec in recs:
            raw = _rec_raw_response(rec)
            assert raw["candidate_id"] != raw["sibling_id"]
            assert raw["candidate_id"] in ("candidate-sym", "sibling-sym")
            assert raw["sibling_id"] in ("candidate-sym", "sibling-sym")


class TestPersistenceCallContract:
    def test_insert_advisor_observation_called_with_correct_role_and_subject(
        self, rr, tmp_path, monkeypatch
    ):
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        recs = rr.build_recommendations(db_file=db_file, days=None)

        captured = []
        monkeypatch.setattr(
            db_module,
            "insert_advisor_observation",
            lambda **kwargs: captured.append(kwargs) or 1,
        )
        rr.persist_recommendations(recs, db_file=db_file)

        assert len(captured) >= 1
        for call_kwargs, rec in zip(captured, recs):
            candidate_id = _rec_raw_response(rec)["candidate_id"]
            assert call_kwargs.get("advisor_role") == "RETIREMENT_RECOMMENDATION"
            assert call_kwargs.get("subject_type") == "symphony"
            assert call_kwargs.get("subject_id") == candidate_id
            assert call_kwargs.get("symphony_id") == candidate_id
            assert call_kwargs.get("verdict") == "retire_candidate"

    def test_persist_recommendations_returns_the_count_persisted(self, rr, tmp_path, monkeypatch):
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        recs = rr.build_recommendations(db_file=db_file, days=None)

        monkeypatch.setattr(db_module, "insert_advisor_observation", lambda **kwargs: 42)
        result = rr.persist_recommendations(recs, db_file=db_file)
        assert result == len(recs)

    def test_persist_recommendations_with_empty_list_persists_nothing(
        self, rr, tmp_path, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            db_module, "insert_advisor_observation", lambda **kwargs: calls.append(1) or 1
        )
        result = rr.persist_recommendations([], db_file=str(tmp_path / "unused.db"))
        assert result == 0
        assert calls == []


class TestPersistenceEndToEndReadback:
    def test_persisted_row_is_advisory_only_via_real_readback(self, rr, tmp_path, monkeypatch):
        """End-to-end (no mocking of insert_advisor_observation): real
        build -> real persist -> real DB readback, confirming
        is_advisory_only == 1 (AC-7's persistence half -- the DB layer itself
        forces this regardless of caller kwargs, but this proves the real
        call actually reaches that code path)."""
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert len(recs) >= 1

        rr.persist_recommendations(recs, db_file=db_file)

        candidate_id = _rec_raw_response(recs[0])["candidate_id"]
        rows = db_module.get_advisor_observations_for_subject("symphony", candidate_id)
        matching = [r for r in rows if r.get("advisor_role") == "RETIREMENT_RECOMMENDATION"]
        assert matching, (
            f"No RETIREMENT_RECOMMENDATION row found for subject {candidate_id!r} "
            "after persist_recommendations -- persistence did not reach the DB."
        )
        assert matching[0].get("is_advisory_only") == 1

    def test_persisted_raw_response_round_trips_through_real_json_storage(
        self, rr, tmp_path, monkeypatch
    ):
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        rr.persist_recommendations(recs, db_file=db_file)

        candidate_id = _rec_raw_response(recs[0])["candidate_id"]
        rows = db_module.get_advisor_observations_for_subject("symphony", candidate_id)
        matching = [r for r in rows if r.get("advisor_role") == "RETIREMENT_RECOMMENDATION"]
        raw = matching[0]["raw_response"]
        assert isinstance(raw, dict)
        missing = _RAW_RESPONSE_KEYS - raw.keys()
        assert not missing, f"Round-tripped raw_response is missing keys: {missing}"


class TestRoleNotInOverviewLoop:
    def test_retirement_recommendation_role_is_not_in_advisor_roles(self):
        """AC-8: 'The new role is NOT added to _ADVISOR_ROLES (app.py) -- it
        stays out of the Overview observations loop.'"""
        import app as app_module

        assert "RETIREMENT_RECOMMENDATION" not in app_module._ADVISOR_ROLES


def _rec_raw_response(rec):
    if isinstance(rec, dict):
        raw = rec.get("raw_response")
        if isinstance(raw, dict):
            return raw
        return rec
    raw = getattr(rec, "raw_response", None)
    if isinstance(raw, dict):
        return raw
    pytest.fail(f"Could not extract a raw_response dict from recommendation item: {rec!r}")


# ---------------------------------------------------------------------------
# PR-level /code-review Finding 5 (retirement_recommender.py's top-level
# `except Exception: return []`): a silent except swallows a genuine crash
# with zero trace -- the nightly scheduler tick would log "0 recommendations"
# indistinguishably whether the run was a real honest-empty result or a
# silent internal failure. PM ruling: log a WARNING with type(exc).__name__
# before returning [] -- never str(exc) (D-1: no internals leaked), matching
# the house convention already used by sibling advisors modules (e.g.
# advisors/incubation.py's `logger.warning("...: %s", type(exc).__name__)`).
# ---------------------------------------------------------------------------


class TestBuildRecommendationsLogsOnInternalFailure:
    def test_internal_exception_is_logged_as_a_warning_before_returning_empty(
        self, rr, tmp_path, monkeypatch, caplog
    ):
        """Currently RED: build_recommendations's top-level except swallows
        the exception with no logging at all -- a real crash and an honest
        'no live symphonies' empty result are indistinguishable in the logs."""
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)

        def _explode(*_a, **_k):
            raise RuntimeError("simulated internal failure")

        monkeypatch.setattr(db_module, "load_state", _explode)

        with caplog.at_level("WARNING", logger="advisors.retirement_recommender"):
            result = rr.build_recommendations(db_file=db_file, days=None)

        assert result == [], (
            "D-1 contract: an internal failure must still degrade to an "
            "honest empty list, never propagate."
        )
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warning_records, (
            "build_recommendations swallowed a RuntimeError with NO logging at "
            "all -- the nightly scheduler tick cannot distinguish a genuine "
            "internal crash from an honest 'no recommendations tonight' "
            "result (PR-level /code-review Finding 5). Expected at least one "
            "WARNING-level log record."
        )
        assert any("RuntimeError" in r.getMessage() for r in warning_records), (
            f"Expected a WARNING record naming the exception type "
            f"('RuntimeError'), got: {[r.getMessage() for r in warning_records]}"
        )

    def test_logged_exception_type_reflects_the_real_exception_class(
        self, rr, tmp_path, monkeypatch, caplog
    ):
        """Proves the log message names type(exc).__name__ DYNAMICALLY, not a
        hardcoded literal -- a different exception class must produce a
        differently-worded log record."""
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)

        def _explode_differently(*_a, **_k):
            raise ValueError("a different simulated failure")

        monkeypatch.setattr(db_module, "load_state", _explode_differently)

        with caplog.at_level("WARNING", logger="advisors.retirement_recommender"):
            rr.build_recommendations(db_file=db_file, days=None)

        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("ValueError" in r.getMessage() for r in warning_records), (
            f"Expected a WARNING record naming 'ValueError' (the real "
            f"exception class raised), got: "
            f"{[r.getMessage() for r in warning_records]}"
        )
        assert not any("RuntimeError" in r.getMessage() for r in warning_records), (
            "Log record named the WRONG exception type -- 'RuntimeError' must "
            "not appear when a ValueError was actually raised (proves the "
            "type name is derived dynamically, not a copy-pasted literal)."
        )

    def test_logged_message_never_leaks_the_exception_string_itself(
        self, rr, tmp_path, monkeypatch, caplog
    ):
        """D-1 contract: only type(exc).__name__ may appear, never str(exc) --
        an exception message could carry a file path, a DB row's raw content,
        or other internal detail that shouldn't reach logs at WARNING+ on a
        production D-1 boundary."""
        db_file = _build_screen_hit_db(tmp_path, monkeypatch)
        secret_marker = "UNIQUE_SENTINEL_STRING_MUST_NOT_LEAK_67891"

        def _explode_with_sensitive_detail(*_a, **_k):
            raise RuntimeError(secret_marker)

        monkeypatch.setattr(db_module, "load_state", _explode_with_sensitive_detail)

        with caplog.at_level("WARNING", logger="advisors.retirement_recommender"):
            rr.build_recommendations(db_file=db_file, days=None)

        for record in caplog.records:
            assert secret_marker not in record.getMessage(), (
                f"Log record leaked the raw exception string ({secret_marker!r}) "
                "-- D-1 contract requires only type(exc).__name__, never "
                "str(exc)."
            )
