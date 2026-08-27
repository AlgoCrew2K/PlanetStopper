"""RED tests -- Cycle 2c AC-4: nightly-explain spend control (reuse a prior
explanation for a materially-unchanged pair instead of re-billing the LLM).

feature-plans/retirement-approval-polish.md AC-4: the 03:45 tick does NOT
re-generate (re-LLM) + re-persist an explanation for a flagged pair whose
recommendation is materially unchanged from the most recent prior explanation
for that same pair -- it reuses the prior explanation. Bounded, non-recurring
metered spend for a persistently-flagged pair. A genuinely new/changed pair
is still explained. Fail-open: if the reuse-lookup fails, fall back to
generating (never silently drop the explanation).

.claude/tdd-handoff.md pins the exact contract (co-designed with ret3-route,
team-lead-approved).

**Superseded by a PR #140 /code-review finding (2026-08-27, team-lead
finding 1), fixed in THIS revision:** the original design (kept here as
history, do not re-implement) compared a curated 8-9 field subset
(correlation/composites/gates/basis_label/names). The bug: advisors.
retirement_explainer._build_explain_messages does json.dumps(THE WHOLE rec)
and instructs the LLM to "ground every claim strictly in the supplied
evidence" -- so the explanation can cite MORE than the curated subset
covered (stressed_correlation, ci_lower/ci_upper, n_obs, and the per-
symphony candidate_metrics/sibling_metrics dicts the composite is derived
from). If one of those UNCOVERED fields drifted materially while the
curated subset stayed stable, the gate would reuse stale prose citing a
now-wrong number -- a real honesty gap, not a hypothetical.

**Fixed contract: whole-dict canonical-snapshot equality, not a curated
field list.** Reuse-eligible iff, vs the most recent PRIOR persisted
RETIREMENT_RECOMMENDATION row for the exact same (candidate_id, sibling_id)
pair (rows are newest-first; first match = most recent):
  1. prior row's raw_response["explanation"] is truthy (never reuse a null)
  2. _canonical_evidence_snapshot(rec) == _canonical_evidence_snapshot(prior_raw)

_canonical_evidence_snapshot(d) = every key in d EXCEPT "explanation" (the
value being reused) and the pair-identity keys "candidate_id"/"sibling_id"
(already matched by construction to find this prior row -- not citable
"evidence" in the honesty sense), with every float rounded to 2 decimal
places, RECURSIVELY into nested dicts (candidate_metrics/sibling_metrics).
None passes through unchanged (two None stressed_correlation values still
match structurally-equal snapshots; one None vs one real value correctly
mismatches, since None != any rounded float). A key missing on either side
means the two snapshots are NOT equal (plain dict `==` is exact key-set +
value) -- this subsumes the freshness-on-rename rule and the legacy-row
rule (a prior row predating this fix, or predating AC-2's name enrichment,
is missing keys the current rec has) for free, with zero special-casing.

This directly satisfies "a reused explanation must never cite a number that
changed since it was generated" for the ENTIRE evidence surface the prompt
actually embeds, not just 8 hand-picked fields -- and is future-proof
against schema growth (a NEW raw_response field automatically joins the
comparison, rather than silently reopening this exact gap again).

n_obs is an int (never rounded, exact equality) -- RETIREMENT_LOOKBACK_DAYS
is a FIXED-SIZE rolling window (not "all history"), so in the steady state
n_obs should stay constant night-to-night for a mature pair; exact equality
is defensible, not a de-facto "never reuse" trap. stressed_correlation/
candidate_metrics/sibling_metrics WILL legitimately force a regen more
often than the old curated design (team-lead's own illustrative drift:
stressed_correlation 0.71 -> 0.66) -- that is the INTENDED, correct
honesty-first behavior, not a bill-protection regression; tolerances are
deliberately NOT loosened to compensate, since that would reopen the exact
gap this fix closes.

The WHOLE reuse-decision (lookup + snapshot-equality comparison) is ONE
unit: any exception anywhere in it resolves to "no match" -> generate
fresh. Tested via a raising accessor (the realistic exception surface --
the snapshot/rounding logic itself degrades gracefully on malformed data
rather than raising, see TestFailOpen's docstrings for why).

Uses advisors.retirement_recommender.build_recommendations /
advisors.retirement_explainer.explain_recommendation as CC-2 lazy-imported
seams (same mocking pattern as test_retirement_producer_explainer_wiring.py
and test_retirement_explainer_name_enrichment.py). The reuse-lookup itself is
expected to go through database.get_advisor_observations_for_role (the same
role-wide read accessor _fetch_retirement_recommendations already uses) --
mocked directly for the per-field unit tests below; the centerpiece spend-
bound test at the bottom uses the REAL DB end-to-end (only build_recommendations/
explain_recommendation mocked) to prove the actual bounded-spend claim, not
just the isolated comparison logic.

Expected state: RED until app.py's _retirement_recommender_tick_worker gains
the reuse-vs-regenerate decision.
"""

from __future__ import annotations

import pytest

_CANDIDATE_HASH = "cand-reuse-aaa"
_CANDIDATE_NAME = "Momentum Alpha Rotation"
_SIBLING_HASH = "sib-reuse-bbb"
_SIBLING_NAME = "Value Tilt Core"

# Fixture-realism check (team-lead's explicit note on the finding-1 fix):
# this key set + nesting is copied verbatim from advisors/retirement_
# recommender.py's real raw_response construction (build_recommendations,
# the `raw_response = {...}` literal) plus AC-2's candidate_name/
# sibling_name additions -- re-verified by reading that module directly
# before writing this fixture, not invented as a plausible superset.
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

    db_path = str(tmp_path / "test_retirement_explanation_reuse.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _run_worker_with_mocked_prior_rows(prior_rows, new_rec, *, explain_return="fresh explanation"):
    """Runs the tick worker with database.get_advisor_observations_for_role
    mocked to return `prior_rows`, build_recommendations mocked to return
    [new_rec], and explain_recommendation mocked/counted. Returns
    (explain_call_count, persisted_recs)."""
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
# Reuse when materially unchanged
# ===========================================================================


class TestReuseWhenUnchanged:
    def test_new_pair_no_prior_history_generates_fresh(self, isolated_db):
        new_rec = _sample_raw_response()
        explain_calls, persisted = _run_worker_with_mocked_prior_rows([], new_rec)

        assert len(explain_calls) == 1, "A genuinely new pair (no prior history) must be explained."
        assert persisted[0]["explanation"] == "fresh explanation"

    def test_reuses_prior_explanation_when_materially_unchanged(self, isolated_db):
        prior_raw = _sample_raw_response()
        prior = _prior_row(1, prior_raw, explanation="last night's explanation")
        new_rec = _sample_raw_response()  # identical values

        explain_calls, persisted = _run_worker_with_mocked_prior_rows([prior], new_rec)

        assert len(explain_calls) == 0, (
            "A materially-unchanged pair must NOT re-call explain_recommendation."
        )
        assert persisted[0]["explanation"] == "last night's explanation", (
            "The reused explanation must be the PRIOR persisted text, not fabricated."
        )

    def test_reuse_tolerates_tiny_rounding_noise_within_2dp(self, isolated_db):
        """Day-to-day correlation/composite recomputation over a rolling
        window will never be bit-identical -- the rounding comparison must
        tolerate sub-hundredth noise."""
        prior_raw = _sample_raw_response(correlation=0.801, candidate_composite=0.204)
        prior = _prior_row(1, prior_raw, explanation="reused text")
        new_rec = _sample_raw_response(correlation=0.804, candidate_composite=0.196)
        # round(0.801,2)==round(0.804,2)==0.80; round(0.204,2)==round(0.196,2)==0.20

        explain_calls, persisted = _run_worker_with_mocked_prior_rows([prior], new_rec)

        assert len(explain_calls) == 0
        assert persisted[0]["explanation"] == "reused text"

    def test_reuse_tolerates_tiny_rounding_noise_across_the_whole_evidence_surface(
        self, isolated_db
    ):
        """Finding 1 fix extension: the tolerance must hold for EVERY
        rounded field the whole-dict snapshot now covers (not just
        correlation/composite) -- day-to-day recomputation noise anywhere
        in the evidence must not force an unnecessary regen."""
        prior_candidate_metrics = {
            "annualized_return": 0.031,
            "sharpe": 0.201,
            "sortino": 0.251,
            "max_drawdown": -0.301,
            "calmar": 0.101,
        }
        new_candidate_metrics = {
            "annualized_return": 0.034,
            "sharpe": 0.204,
            "sortino": 0.254,
            "max_drawdown": -0.304,
            "calmar": 0.104,
        }
        # Every pair below rounds identically to 2dp.
        prior_raw = _sample_raw_response(
            correlation=0.801,
            ci_lower=0.721,
            ci_upper=0.861,
            candidate_composite=0.204,
            sibling_composite=0.751,
            stressed_correlation=0.781,
            holdings_overlap=0.101,
            candidate_metrics=prior_candidate_metrics,
        )
        prior = _prior_row(1, prior_raw, explanation="reused across the board")
        new_rec = _sample_raw_response(
            correlation=0.804,
            ci_lower=0.724,
            ci_upper=0.864,
            candidate_composite=0.196,
            sibling_composite=0.754,
            stressed_correlation=0.784,
            holdings_overlap=0.104,
            candidate_metrics=new_candidate_metrics,
        )

        explain_calls, persisted = _run_worker_with_mocked_prior_rows([prior], new_rec)

        assert len(explain_calls) == 0, (
            "Sub-hundredth noise across the whole evidence surface must not force a regen."
        )
        assert persisted[0]["explanation"] == "reused across the board"

    def test_reuses_when_stressed_correlation_and_holdings_overlap_are_none_on_both_sides(
        self, isolated_db
    ):
        """Corroborating None-handling: two genuinely-matching None values
        must NOT force a spurious regen -- None is a valid, comparable
        evidence state (the stress sub-window is too thin / holdings are
        off-hours-unavailable on both nights), not an automatic mismatch."""
        prior_raw = _sample_raw_response(stressed_correlation=None, holdings_overlap=None)
        prior = _prior_row(1, prior_raw, explanation="reused despite nones")
        new_rec = _sample_raw_response(stressed_correlation=None, holdings_overlap=None)

        explain_calls, persisted = _run_worker_with_mocked_prior_rows([prior], new_rec)

        assert len(explain_calls) == 0
        assert persisted[0]["explanation"] == "reused despite nones"


# ===========================================================================
# Regenerate when materially changed (one dimension at a time)
# ===========================================================================


class TestRegenerateWhenChanged:
    def test_regenerates_when_correlation_materially_changed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(correlation=0.80))
        new_rec = _sample_raw_response(correlation=0.95)  # rounds to 0.95 vs 0.80

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_candidate_composite_changed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(candidate_composite=0.20))
        new_rec = _sample_raw_response(candidate_composite=0.60)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_sibling_composite_changed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(sibling_composite=0.75))
        new_rec = _sample_raw_response(sibling_composite=0.10)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_uncertainty_gate_flips(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(uncertainty_gate_passed=True))
        new_rec = _sample_raw_response(uncertainty_gate_passed=False)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_structural_redundancy_gate_flips(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(structural_redundancy_gate_passed=True))
        new_rec = _sample_raw_response(structural_redundancy_gate_passed=False)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_basis_label_changed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(basis_label="actual-traded (bot) daily returns"))
        new_rec = _sample_raw_response(basis_label="something-else")

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_stressed_correlation_changed(self, isolated_db):
        """Finding 1 (PR#140 /code-review): the explainer prompt embeds the
        WHOLE rec, including stressed_correlation -- the LLM could cite it
        even though the OLD curated field-set never compared it. Team-lead's
        own illustrative drift: 0.71 -> 0.66 (both pass the structural gate,
        rounded composites/correlation/gates unchanged -- exactly the case
        that slipped through pre-fix)."""
        prior = _prior_row(1, _sample_raw_response(stressed_correlation=0.71))
        new_rec = _sample_raw_response(stressed_correlation=0.66)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_ci_lower_changed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(ci_lower=0.72))
        new_rec = _sample_raw_response(ci_lower=0.55)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_ci_upper_changed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(ci_upper=0.86))
        new_rec = _sample_raw_response(ci_upper=0.99)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_n_obs_changed(self, isolated_db):
        """n_obs is compared EXACTLY (an int, _round_floats leaves it
        untouched) -- RETIREMENT_LOOKBACK_DAYS is a fixed-size rolling
        window, so in the steady state n_obs should stay constant night-to-
        night for a mature pair; a genuine change signals a real data-
        availability shift (e.g. a gap) worth re-explaining, not noise."""
        prior = _prior_row(1, _sample_raw_response(n_obs=200))
        new_rec = _sample_raw_response(n_obs=150)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_candidate_metric_changed(self, isolated_db):
        """One of the 5 per-symphony metrics the composite is derived from
        -- the explainer prompt embeds the whole candidate_metrics dict
        verbatim, so the LLM could cite an individual metric (e.g. Sharpe)
        even though the OLD curated field-set only compared the aggregate
        composite, not its inputs."""
        prior_metrics = dict(_DEFAULT_CANDIDATE_METRICS)
        new_metrics = dict(_DEFAULT_CANDIDATE_METRICS)
        new_metrics["sharpe"] = 0.99  # materially different from the 0.20 default

        prior = _prior_row(1, _sample_raw_response(candidate_metrics=prior_metrics))
        new_rec = _sample_raw_response(candidate_metrics=new_metrics)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_sibling_metric_changed(self, isolated_db):
        prior_metrics = dict(_DEFAULT_SIBLING_METRICS)
        new_metrics = dict(_DEFAULT_SIBLING_METRICS)
        new_metrics["calmar"] = 0.01

        prior = _prior_row(1, _sample_raw_response(sibling_metrics=prior_metrics))
        new_rec = _sample_raw_response(sibling_metrics=new_metrics)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_holdings_overlap_changed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(holdings_overlap=0.10))
        new_rec = _sample_raw_response(holdings_overlap=0.90)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_stressed_correlation_goes_from_none_to_a_real_value(
        self, isolated_db
    ):
        """None-handling: stressed_correlation is Optional (None when the
        stress sub-window is too thin, per evaluate_structural_redundancy_
        gate's own fail-closed contract) -- a transition from None to a real
        value (or vice versa) is itself material and must force a regen,
        never crash the comparison (None has no __round__)."""
        prior = _prior_row(1, _sample_raw_response(stressed_correlation=None))
        new_rec = _sample_raw_response(stressed_correlation=0.70)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_prior_row_predates_finding1_evidence_fields(self, isolated_db):
        """General principle beyond just names: ANY missing evidence key on
        the prior row (e.g. a row persisted before this fix shipped,
        carrying only the OLD curated field subset) must be treated as
        changed -- can't prove "no drift" on evidence it never recorded.
        Proves the whole-dict-equality design's missing-key handling holds
        for the finding-1 fields specifically, not just names (see
        test_regenerates_when_prior_row_predates_name_enrichment below)."""
        old_shape_raw = {
            "candidate_id": _CANDIDATE_HASH,
            "sibling_id": _SIBLING_HASH,
            "correlation": 0.80,
            "candidate_composite": 0.20,
            "sibling_composite": 0.75,
            "uncertainty_gate_passed": True,
            "structural_redundancy_gate_passed": True,
            "basis_label": "actual-traded (bot) daily returns",
            "candidate_name": _CANDIDATE_NAME,
            "sibling_name": _SIBLING_NAME,
            # ci_lower/ci_upper/n_obs/candidate_metrics/sibling_metrics/
            # stressed_correlation/holdings_overlap all absent -- the exact
            # shape a pre-finding-1 persisted row would have.
        }
        prior = _prior_row(1, old_shape_raw)
        new_rec = _sample_raw_response()  # the full, current schema

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_candidate_renamed(self, isolated_db):
        """Freshness-on-rename ruling: a rename forces regen even if every
        other schema field is unchanged -- reused prose must never describe
        a symphony by a name it no longer has."""
        prior = _prior_row(1, _sample_raw_response(candidate_name="Old Candidate Name"))
        new_rec = _sample_raw_response(candidate_name="New Candidate Name")

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_sibling_renamed(self, isolated_db):
        prior = _prior_row(1, _sample_raw_response(sibling_name="Old Sibling Name"))
        new_rec = _sample_raw_response(sibling_name="New Sibling Name")

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_regenerates_when_prior_row_predates_name_enrichment(self, isolated_db):
        """A legacy prior row persisted before AC-2 shipped has no
        candidate_name/sibling_name key at all -- can't prove "no rename"
        happened, so it must NOT be treated as unchanged."""
        legacy_raw = _sample_raw_response()
        del legacy_raw["candidate_name"]
        del legacy_raw["sibling_name"]
        prior = _prior_row(1, legacy_raw)
        new_rec = _sample_raw_response()  # otherwise identical

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1

    def test_never_reuses_a_null_prior_explanation(self, isolated_db):
        """A prior night where the LLM itself failed (explanation=None) must
        never be treated as reusable -- a null is not a real explanation."""
        prior = _prior_row(1, _sample_raw_response(), explanation=None)
        new_rec = _sample_raw_response()  # otherwise identical

        explain_calls, persisted = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1, "A null prior explanation must never be reused."
        assert persisted[0]["explanation"] == "fresh explanation"

    def test_pair_identity_requires_exact_sibling_match(self, isolated_db):
        """A prior row for the SAME candidate_id but a DIFFERENT sibling_id
        is a different pair entirely -- must not be treated as history for
        this pair."""
        prior = _prior_row(1, _sample_raw_response(sibling_id="sib-totally-different"))
        new_rec = _sample_raw_response(sibling_id=_SIBLING_HASH)

        explain_calls, _ = _run_worker_with_mocked_prior_rows([prior], new_rec)
        assert len(explain_calls) == 1


class TestMostRecentPriorRowGoverns:
    def test_uses_the_most_recent_prior_row_when_multiple_exist(self, isolated_db):
        """Two prior rows exist for the same pair (older + newer). The
        accessor contract returns newest-first; the DECISION must follow the
        row appearing FIRST in that list, not an arbitrary/oldest match.
        Constructed so the two rows' outcomes actively disagree, so this
        test discriminates "uses first-in-list" from "uses some other row"."""
        recent_matching = _prior_row(5, _sample_raw_response(correlation=0.80))
        older_mismatched = _prior_row(1, _sample_raw_response(correlation=0.10))
        new_rec = _sample_raw_response(correlation=0.80)  # matches the RECENT row only

        explain_calls, persisted = _run_worker_with_mocked_prior_rows(
            [recent_matching, older_mismatched], new_rec
        )

        assert len(explain_calls) == 0, (
            "Expected the most recent prior row (correlation 0.80, a material match) "
            "to govern the reuse decision, not the older mismatched row."
        )
        assert persisted[0]["explanation"] == "prior explanation text"


# ===========================================================================
# Fail-open: any failure in the reuse decision falls back to fresh generation
# ===========================================================================


class TestFailOpen:
    def test_reuse_lookup_raising_falls_back_to_fresh_generation(self, isolated_db):
        import advisors.retirement_explainer as explainer_module
        import advisors.retirement_recommender as recommender_module
        import app as app_module
        import database as db_module

        new_rec = _sample_raw_response()
        explain_calls: list[dict] = []
        persisted: list[dict] = []

        def _fake_explain(r):
            explain_calls.append(dict(r))
            return "fresh explanation"

        def _fake_persist(recs, **kw):
            persisted.extend(recs)
            return len(recs)

        def _raise(*a, **kw):
            raise RuntimeError("simulated DB read failure")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(recommender_module, "build_recommendations", lambda **kw: [new_rec])
            mp.setattr(recommender_module, "persist_recommendations", _fake_persist)
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)
            mp.setattr(db_module, "get_advisor_observations_for_role", _raise)

            try:
                app_module._retirement_recommender_tick_worker()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"_retirement_recommender_tick_worker must never propagate a "
                    f"reuse-lookup failure: {type(exc).__name__}: {exc}"
                )

        assert len(explain_calls) == 1, (
            "A reuse-lookup failure must fail OPEN (generate fresh), never silently "
            "drop the explanation."
        )
        assert persisted[0]["explanation"] == "fresh explanation"

    def test_malformed_prior_row_falls_back_to_fresh_generation(self, isolated_db):
        """A different SHAPE reaching the same safe outcome: the prior row
        exists and the accessor call succeeds, but its raw_response is
        missing nearly every field the comparison needs (e.g. a severely
        corrupt/malformed row). Under the whole-dict-snapshot design this
        degrades via a plain snapshot INEQUALITY (an empty-vs-populated
        dict, never a raised exception -- _round_floats passes through
        anything it doesn't recognize rather than crashing on it), not the
        try/except fail-open path -- still proves the same safe end-to-end
        outcome (never reuse, never crash the worker), just via a different
        internal mechanism than the accessor-raises case above."""
        malformed_prior = {
            "id": 1,
            "created_at": "2026-08-20T03:45:00Z",
            "advisor_role": "RETIREMENT_RECOMMENDATION",
            "subject_type": "symphony",
            "subject_id": _CANDIDATE_HASH,
            "verdict": "retire_candidate",
            "raw_response": {
                "candidate_id": _CANDIDATE_HASH,
                "sibling_id": _SIBLING_HASH,
                # correlation/composites/gates/basis_label/names all absent
                "explanation": "some prior text",
            },
            "is_advisory_only": 1,
            "spec_bundle_id": None,
            "symphony_id": _CANDIDATE_HASH,
        }
        new_rec = _sample_raw_response()

        explain_calls, persisted = _run_worker_with_mocked_prior_rows(
            [malformed_prior], new_rec, explain_return="fresh explanation"
        )

        assert len(explain_calls) == 1, "A malformed prior row must degrade to fresh generation."
        assert persisted[0]["explanation"] == "fresh explanation"


# ===========================================================================
# Centerpiece: real end-to-end bounded-spend proof over consecutive nights
# ===========================================================================


class TestBoundedSpendEndToEnd:
    def test_three_consecutive_nights_unchanged_pair_calls_explainer_exactly_once(
        self, isolated_db
    ):
        """The actual claim AC-4 makes: 'bounded, non-recurring metered spend
        for a persistently-flagged pair.' Real DB (only build_recommendations/
        explain_recommendation mocked -- persist_recommendations and the
        reuse-lookup accessor are the REAL production functions, so this
        proves genuine end-to-end behavior, not just the isolated comparison
        logic above)."""
        import advisors.retirement_explainer as explainer_module
        import advisors.retirement_recommender as recommender_module
        import app as app_module

        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})

        explain_call_count = {"n": 0}

        def _fake_explain(r):
            explain_call_count["n"] += 1
            return f"explanation #{explain_call_count['n']}"

        def _fixed_recs(**kw):
            # A fresh dict each call (simulating a real nightly recompute),
            # but with byte-identical VALUES each night.
            return [_sample_raw_response()]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(recommender_module, "build_recommendations", lambda **kw: _fixed_recs())
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()  # night 1
            app_module._retirement_recommender_tick_worker()  # night 2
            app_module._retirement_recommender_tick_worker()  # night 3

        assert explain_call_count["n"] == 1, (
            f"Expected explain_recommendation to be called exactly ONCE across 3 "
            f"consecutive nights of an unchanged pair, got {explain_call_count['n']}."
        )

    def test_mixed_batch_reuse_and_fresh_are_independent_per_pair(self, isolated_db):
        """A tick with TWO pairs -- one persistently flagged (should reuse),
        one brand new (should explain) -- must decide each independently."""
        import advisors.retirement_explainer as explainer_module
        import advisors.retirement_recommender as recommender_module
        import app as app_module

        stable_candidate, stable_sibling = _CANDIDATE_HASH, _SIBLING_HASH
        new_candidate, new_sibling = "cand-brandnew", "sib-brandnew"
        _seed_named_roster(
            {
                stable_candidate: _CANDIDATE_NAME,
                stable_sibling: _SIBLING_NAME,
                new_candidate: "Brand New Candidate",
                new_sibling: "Brand New Sibling",
            }
        )

        explained_candidates: list[str] = []

        def _fake_explain(r):
            explained_candidates.append(r["candidate_id"])
            return f"explanation for {r['candidate_id']}"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                recommender_module,
                "build_recommendations",
                lambda **kw: [_sample_raw_response(stable_candidate, stable_sibling)],
            )
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)
            app_module._retirement_recommender_tick_worker()  # night 1 for the stable pair

        explained_candidates.clear()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                recommender_module,
                "build_recommendations",
                lambda **kw: [
                    _sample_raw_response(stable_candidate, stable_sibling),  # unchanged
                    _sample_raw_response(new_candidate, new_sibling),  # brand new
                ],
            )
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)
            app_module._retirement_recommender_tick_worker()  # night 2

        assert explained_candidates == [new_candidate], (
            f"Expected ONLY the brand-new pair to be explained on night 2 (the stable "
            f"pair should reuse), got {explained_candidates!r}."
        )
