"""RED tests -- Cycle 2d AC-2: explanation-precision == gate-precision (PR#140
2nd /code-review findings 2+3).

feature-plans/retirement-approval-polish-2d.md AC-2: eliminate the
within-rounding-tolerance stale citation (finding 3) AND restore reuse for
stable pairs (finding 2), by making the precision the explanation CITES match
the precision the reuse gate COMPARES. The tick worker rounds a rec's numeric
evidence to `_MATERIAL_CHANGE_ROUND_NDIGITS` (2dp, the existing 2c constant)
BEFORE `explain_recommendation` sees it AND before it is persisted -- so:

  (a) a freshly-generated explanation can never cite a number more precise
      than what gets persisted/rendered (closes finding 3's <=0.005 stale
      window -- the explainer never sees the un-rounded value at all), and
  (b) reuse fires whenever the 2dp-rounded evidence is unchanged night-to-
      night, even though build_recommendations legitimately returns
      slightly different RAW floats each night (real walk-forward
      recomputation over a rolling window is never bit-identical) --
      restoring finding 2's spend savings for a genuinely stable pair.

Invariant under test: a reused OR freshly-generated explanation never cites a
number that differs from the current (rounded) evidence -- proven via the
strongest testable proxy: the exact dict explain_recommendation receives
(minus "explanation") must equal the exact dict persist_recommendations
receives (minus "explanation") -- byte-identical, not just "close enough".

A genuinely-changed (post-rounding) pair still regenerates -- honest, not a
tolerance loosened to compensate. Fail-open (any reuse-lookup failure ->
fresh generation) is unchanged from Cycle 2c -- not re-tested here, see
test_retirement_explanation_reuse.py::TestFailOpen.

Fixture/helper pattern (`_sample_raw_response`/`_prior_row`/
`_run_worker_with_mocked_prior_rows`) duplicated from tests/app/
test_retirement_explanation_reuse.py per this repo's established
fixtures-are-not-cross-file-shared convention. Verified test_retirement_
explanation_reuse.py itself needs NO changes for this cycle: every fixture
literal it uses is already <=2dp (correlation=0.80, sharpe=0.20, etc.), so
2dp rounding is a byte-identical no-op there; its non-2dp literals (0.801/
0.804 etc.) are hand-constructed "prior row" fixtures injected directly into
the mocked database.get_advisor_observations_for_role accessor, never
flowing through the tick worker's own persist path, so they are unaffected
by this rounding-before-persist change.

Expected state: RED until app.py's _retirement_recommender_tick_worker
rounds each rec's numeric evidence to 2dp before the reuse-or-explain
decision.
"""

from __future__ import annotations

import pytest

_CANDIDATE_HASH = "cand-precision-aaa"
_CANDIDATE_NAME = "Momentum Alpha Rotation"
_SIBLING_HASH = "sib-precision-bbb"
_SIBLING_NAME = "Value Tilt Core"

# Verified against advisors/retirement_recommender.py's real raw_response
# construction (build_recommendations) plus AC-2's (Cycle 2c) candidate_name/
# sibling_name additions -- same fixture shape as test_retirement_
# explanation_reuse.py, duplicated per this repo's convention.
_DEFAULT_CANDIDATE_METRICS = {
    "annualized_return": 0.03,
    "sharpe": 0.20,
    "sortino": 0.25,
    "max_drawdown": -0.30,
    "calmar": 0.10,
}
_DEFAULT_SIBLING_METRICS = {
    "annualized_return": 0.18,
    "sharpe": 1.40,
    "sortino": 1.80,
    "max_drawdown": -0.06,
    "calmar": 3.00,
}


def _sample_raw_response(
    candidate_id=_CANDIDATE_HASH,
    sibling_id=_SIBLING_HASH,
    *,
    correlation=0.80,
    ci_lower=0.72,
    ci_upper=0.86,
    n_obs=200,
    candidate_composite=0.20,
    sibling_composite=0.75,
    candidate_metrics=None,
    sibling_metrics=None,
    uncertainty_gate_passed=True,
    structural_redundancy_gate_passed=True,
    stressed_correlation=0.78,
    holdings_overlap=0.10,
    basis_label="actual-traded (bot) daily returns",
    candidate_name=_CANDIDATE_NAME,
    sibling_name=_SIBLING_NAME,
):
    return {
        "candidate_id": candidate_id,
        "sibling_id": sibling_id,
        "correlation": correlation,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "n_obs": n_obs,
        "candidate_composite": candidate_composite,
        "sibling_composite": sibling_composite,
        "candidate_metrics": (
            dict(candidate_metrics)
            if candidate_metrics is not None
            else dict(_DEFAULT_CANDIDATE_METRICS)
        ),
        "sibling_metrics": (
            dict(sibling_metrics) if sibling_metrics is not None else dict(_DEFAULT_SIBLING_METRICS)
        ),
        "uncertainty_gate_passed": uncertainty_gate_passed,
        "structural_redundancy_gate_passed": structural_redundancy_gate_passed,
        "stressed_correlation": stressed_correlation,
        "holdings_overlap": holdings_overlap,
        "basis_label": basis_label,
        "candidate_name": candidate_name,
        "sibling_name": sibling_name,
    }


def _prior_row(row_id, raw_response, *, explanation="prior explanation text"):
    """A fake row shaped exactly like database.get_advisor_observations_for_role's
    return value (see database._ADVISOR_OBSERVATION_COLUMNS)."""
    rr = dict(raw_response)
    rr["explanation"] = explanation
    return {
        "id": row_id,
        "created_at": "2026-08-20T03:45:00Z",
        "advisor_role": "RETIREMENT_RECOMMENDATION",
        "subject_type": "symphony",
        "subject_id": rr["candidate_id"],
        "verdict": "retire_candidate",
        "raw_response": rr,
        "is_advisory_only": 1,
        "spec_bundle_id": None,
        "symphony_id": rr["candidate_id"],
    }


def _seed_named_roster(id_to_name: dict):
    import database as db_module

    roster = {sid: {"name": name, "logic_holdings": {}} for sid, name in id_to_name.items()}
    db_module.save_state(roster)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database as db_module
    from database import init_db, run_migrations

    db_path = str(tmp_path / "test_retirement_explanation_precision_consistency.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _run_worker_with_mocked_prior_rows(prior_rows, new_rec, *, explain_return="fresh explanation"):
    """Runs the tick worker with database.get_advisor_observations_for_role
    mocked to return `prior_rows`, build_recommendations mocked to return
    [new_rec], and explain_recommendation mocked/counted. Returns
    (explain_calls, persisted_recs) -- explain_calls is a list of dict
    COPIES of whatever explain_recommendation was called with, so later
    mutation of the live rec doesn't retroactively change what was
    captured."""
    import advisors.retirement_explainer as explainer_module
    import advisors.retirement_recommender as recommender_module
    import app as app_module
    import database as db_module

    explain_calls: list[dict] = []
    persisted: list[dict] = []

    def _fake_explain(r):
        explain_calls.append(dict(r))
        return explain_return

    def _fake_persist(recs, **kw):
        persisted.extend(recs)
        return len(recs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(recommender_module, "build_recommendations", lambda **kw: [new_rec])
        mp.setattr(recommender_module, "persist_recommendations", _fake_persist)
        mp.setattr(explainer_module, "explain_recommendation", _fake_explain)
        mp.setattr(db_module, "get_advisor_observations_for_role", lambda *a, **kw: prior_rows)

        app_module._retirement_recommender_tick_worker()

    return explain_calls, persisted


# ===========================================================================
# The explainer must see already-rounded evidence
# ===========================================================================


class TestExplainerSeesRoundedEvidence:
    def test_rec_passed_to_explainer_is_rounded_to_2dp(self, isolated_db):
        raw_rec = _sample_raw_response(correlation=0.8043219, candidate_composite=0.1958234)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([], raw_rec)

        assert len(explain_calls) == 1
        assert explain_calls[0]["correlation"] == 0.80, (
            f"Expected explain_recommendation to see the ROUNDED correlation "
            f"(0.80), got {explain_calls[0]['correlation']!r} -- the LLM must "
            "never be shown more precision than the persisted/rendered evidence."
        )
        assert explain_calls[0]["candidate_composite"] == 0.20

    def test_nested_candidate_metrics_are_rounded_before_the_explainer_sees_them(self, isolated_db):
        raw_rec = _sample_raw_response(
            candidate_metrics={
                "annualized_return": 0.0312,
                "sharpe": 0.2043,
                "sortino": 0.2519,
                "max_drawdown": -0.3014,
                "calmar": 0.1032,
            }
        )

        explain_calls, _ = _run_worker_with_mocked_prior_rows([], raw_rec)

        assert explain_calls[0]["candidate_metrics"] == {
            "annualized_return": 0.03,
            "sharpe": 0.20,
            "sortino": 0.25,
            "max_drawdown": -0.30,
            "calmar": 0.10,
        }


# ===========================================================================
# Persisted evidence is rounded regardless of branch (fresh OR reused)
# ===========================================================================


class TestPersistedEvidenceIsRounded:
    def test_persisted_rec_evidence_is_rounded_to_2dp_on_fresh_explanation(self, isolated_db):
        raw_rec = _sample_raw_response(correlation=0.8043219, sibling_composite=0.7511932)

        _, persisted = _run_worker_with_mocked_prior_rows([], raw_rec)

        assert persisted[0]["correlation"] == 0.80
        assert persisted[0]["sibling_composite"] == 0.75

    def test_persisted_rec_evidence_is_rounded_even_when_explanation_is_reused(self, isolated_db):
        """Rounding must happen BEFORE the reuse-vs-fresh branch, not only on
        the fresh-explanation path -- a reused explanation's persisted
        numbers must still match the 2dp precision the prose was ever
        grounded in, never the raw un-rounded value build_recommendations
        happened to return that night."""
        prior_raw = _sample_raw_response(correlation=0.80)  # already-rounded (steady state)
        prior = _prior_row(1, prior_raw, explanation="prior text")
        new_raw_unrounded = _sample_raw_response(correlation=0.8034219)  # rounds to 0.80

        explain_calls, persisted = _run_worker_with_mocked_prior_rows([prior], new_raw_unrounded)

        assert len(explain_calls) == 0, "Expected reuse to fire (rounded evidence matches)."
        assert persisted[0]["correlation"] == 0.80, (
            "Even on a REUSED explanation, the persisted correlation must be "
            "rounded to 2dp (0.80), not the raw 0.8034219 build_recommendations "
            "returned -- otherwise the card/API would show a value more precise "
            "than what the reused prose was ever grounded in."
        )

    def test_n_obs_stays_an_exact_int_never_coerced_to_float(self, isolated_db):
        raw_rec = _sample_raw_response(n_obs=187)

        explain_calls, persisted = _run_worker_with_mocked_prior_rows([], raw_rec)

        assert explain_calls[0]["n_obs"] == 187
        assert isinstance(explain_calls[0]["n_obs"], int), (
            "n_obs must never be coerced to a float by the rounding step."
        )
        assert persisted[0]["n_obs"] == 187
        assert isinstance(persisted[0]["n_obs"], int)


# ===========================================================================
# Card/API/prose consistency: the explainer sees EXACTLY what gets persisted
# ===========================================================================


class TestExplainerInputMatchesPersistedEvidence:
    def test_persisted_evidence_equals_the_evidence_the_explainer_actually_saw(self, isolated_db):
        """The strongest testable proxy for 'the LLM never cites a number
        that then renders differently': the exact dict explain_recommendation
        was called with (minus the explanation key itself, which doesn't
        exist yet at call time) must be byte-identical to what ends up
        persisted (minus the now-filled-in explanation)."""
        raw_rec = _sample_raw_response(
            correlation=0.8043219,
            stressed_correlation=0.7834219,
            holdings_overlap=0.1023,
            candidate_metrics={
                "annualized_return": 0.0312,
                "sharpe": 0.2043,
                "sortino": 0.2519,
                "max_drawdown": -0.3014,
                "calmar": 0.1032,
            },
        )

        explain_calls, persisted = _run_worker_with_mocked_prior_rows(
            [], raw_rec, explain_return="fresh text"
        )

        assert len(explain_calls) == 1
        assert len(persisted) == 1
        seen_by_explainer = {k: v for k, v in explain_calls[0].items() if k != "explanation"}
        persisted_evidence = {k: v for k, v in persisted[0].items() if k != "explanation"}
        assert seen_by_explainer == persisted_evidence, (
            "The evidence the explainer was shown must be byte-identical to what "
            "gets persisted (and therefore rendered on the card/API) -- otherwise "
            "the explanation could cite a number that then displays differently.\n"
            f"Explainer saw: {seen_by_explainer!r}\nPersisted: {persisted_evidence!r}"
        )


# ===========================================================================
# Reuse restored for a pair whose RAW recomputation drifts but rounds
# identically; regenerate still fires on genuine (post-rounding) drift
# ===========================================================================


class TestSteadyStateFullPrecisionRecomputation:
    def test_reuse_restored_when_raw_recomputation_noise_rounds_identically_night_to_night(
        self, isolated_db
    ):
        """Finding-2 core claim: build_recommendations legitimately returns
        SLIGHTLY different raw floats each night (real walk-forward
        recomputation over a rolling window is never bit-identical). Real
        end-to-end (only build_recommendations/explain_recommendation
        mocked, real DB persist + real reuse-lookup) across 2 real tick
        calls."""
        import advisors.retirement_explainer as explainer_module
        import advisors.retirement_recommender as recommender_module
        import app as app_module

        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})

        explain_call_count = {"n": 0}

        def _fake_explain(r):
            explain_call_count["n"] += 1
            return f"explanation #{explain_call_count['n']}"

        night_values = iter([0.8012, 0.8043])  # both round to 0.80

        def _fixed_recs(**kw):
            return [_sample_raw_response(correlation=next(night_values))]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(recommender_module, "build_recommendations", lambda **kw: _fixed_recs())
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()  # night 1
            app_module._retirement_recommender_tick_worker()  # night 2

        assert explain_call_count["n"] == 1, (
            f"Expected exactly ONE explain_recommendation call across 2 nights of raw "
            f"recomputation noise that rounds identically to 2dp, got "
            f"{explain_call_count['n']} -- finding-2's spend savings must be restored."
        )

    def test_regenerates_when_raw_recomputation_genuinely_drifts_beyond_2dp(self, isolated_db):
        import advisors.retirement_explainer as explainer_module
        import advisors.retirement_recommender as recommender_module
        import app as app_module

        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})

        explain_call_count = {"n": 0}

        def _fake_explain(r):
            explain_call_count["n"] += 1
            return f"explanation #{explain_call_count['n']}"

        night_values = iter([0.80, 0.95])  # genuinely different at 2dp

        def _fixed_recs(**kw):
            return [_sample_raw_response(correlation=next(night_values))]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(recommender_module, "build_recommendations", lambda **kw: _fixed_recs())
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()  # night 1
            app_module._retirement_recommender_tick_worker()  # night 2

        assert explain_call_count["n"] == 2, (
            "A genuine (post-rounding) drift must still force a regen on night 2 -- "
            "honesty is never traded away for spend savings."
        )


# ===========================================================================
# 2c -> 2d transition: a FULL-PRECISION prior row (the exact shape a
# pre-2d persisted row would have) must still reuse-match against a current
# rec that rounds identically -- proves finding-2's spend savings hold on
# night ONE of the 2d rollout, not only once every prior row has itself
# already been rounded.
# ===========================================================================


class TestFullPrecisionPriorTransition:
    def test_reuse_fires_when_prior_is_full_precision_and_current_rounds_identically(
        self, isolated_db
    ):
        prior_raw = _sample_raw_response(
            correlation=0.8043219,
            candidate_composite=0.1958234,
            sibling_composite=0.7511932,
            ci_lower=0.7211,
            ci_upper=0.8611,
            stressed_correlation=0.7834219,
            holdings_overlap=0.1023,
            candidate_metrics={
                "annualized_return": 0.0312,
                "sharpe": 0.2043,
                "sortino": 0.2519,
                "max_drawdown": -0.3014,
                "calmar": 0.1032,
            },
        )
        prior = _prior_row(1, prior_raw, explanation="pre-2d full-precision explanation")
        # The file's default already-2dp values -- rounds identically to
        # every full-precision field above (verified: 0.8043219->0.80,
        # 0.1958234->0.20, 0.7511932->0.75, 0.7211->0.72, 0.8611->0.86,
        # 0.7834219->0.78, 0.1023->0.10, and each candidate_metrics entry).
        new_rec = _sample_raw_response()

        explain_calls, persisted = _run_worker_with_mocked_prior_rows(
            [prior], new_rec, explain_return="should never be called"
        )

        assert len(explain_calls) == 0, (
            "A full-precision PRIOR row (the exact shape a pre-2d persisted row "
            "would have) whose 2dp-rounded evidence matches the current rec's "
            "must still fire reuse -- the 2c->2d transition must not cost one "
            "extra LLM call per already-stable pair."
        )
        assert persisted[0]["explanation"] == "pre-2d full-precision explanation"
