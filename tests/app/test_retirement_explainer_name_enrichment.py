"""RED tests -- Cycle 2c AC-2: display names threaded into the explainer prompt.

feature-plans/retirement-approval-polish.md AC-2: the 03:45 tick worker
(app._retirement_recommender_tick_worker) enriches each recommendation dict
with candidate_name/sibling_name (resolved from database.load_state()) BEFORE
calling advisors.retirement_explainer.explain_recommendation, so the fable-5
prompt is grounded in readable names and the persisted raw_response.explanation
names the symphonies. Honest fallback to the hash in the prompt when a name is
unresolvable. advisors/retirement_recommender.py stays byte-frozen (LLM-free)
-- pinned by tests/app/test_retirement_producer_explainer_wiring.py's golden
sha256 guard, not duplicated here.

.claude/tdd-handoff.md pins the exact contract (see AC-4's material-change
handoff too -- AC-4's reuse decision needs candidate_name/sibling_name to
ALSO land in the persisted raw_response, not stay transient in-memory, so a
future rename can be detected against a prior night's persisted row).

Pinned key names: rec["candidate_name"], rec["sibling_name"]. Honest
fallback (unresolvable OR a bot_state load failure) = the raw id itself
(mirrors advisors.frontrunner_builder.resolve_incumbent_display_name's own
fallback semantic) -- NEVER None, never a crash.

Lazy-import mocking note (see test_retirement_producer_explainer_wiring.py):
_retirement_recommender_tick_worker imports build_recommendations/
persist_recommendations/explain_recommendation INSIDE the function body
(CC-2) -- patching the source modules' attributes before calling the worker
is picked up correctly.

Expected state: RED until app.py's _retirement_recommender_tick_worker
resolves and stamps candidate_name/sibling_name before calling
explain_recommendation.
"""

from __future__ import annotations

import logging

import pytest

from tests._anthropic_http_fake import AnthropicHttpFake, load_fixture
from tests._anthropic_site_harness import (
    message_body,
    newest_id,
    older_id,
    served_by,
    text_of,
)
from tests.app._retirement_run_summary_stub import with_summary
from tests.llm_request.conftest import error_outcome

_CANDIDATE_HASH = "cand-enrich-aaa"
_CANDIDATE_NAME = "Momentum Alpha Rotation"
_SIBLING_HASH = "sib-enrich-bbb"
_SIBLING_NAME = "Value Tilt Core"


def _sample_rec(candidate_id=_CANDIDATE_HASH, sibling_id=_SIBLING_HASH):
    return {
        "candidate_id": candidate_id,
        "sibling_id": sibling_id,
        "correlation": 0.80,
        "candidate_composite": 0.20,
        "sibling_composite": 0.75,
        "uncertainty_gate_passed": True,
        "structural_redundancy_gate_passed": True,
        "basis_label": "actual-traded (bot) daily returns",
    }


def _seed_named_roster(id_to_name: dict):
    import database as db_module

    roster = {sid: {"name": name, "logic_holdings": {}} for sid, name in id_to_name.items()}
    db_module.save_state(roster)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database as db_module
    from database import init_db, run_migrations

    db_path = str(tmp_path / "test_retirement_explainer_name_enrichment.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


class TestExplainerReceivesResolvedNames:
    def test_explainer_receives_resolved_candidate_and_sibling_names(self, isolated_db):
        import app as app_module

        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        rec = _sample_rec()
        captured_calls: list[dict] = []

        def _fake_explain(r):
            captured_calls.append(dict(r))
            return "explanation text"

        with (
            pytest.MonkeyPatch.context() as mp,
        ):
            import advisors.retirement_explainer as explainer_module
            import advisors.retirement_recommender as recommender_module

            mp.setattr(
                recommender_module,
                "build_recommendations_with_summary",
                with_summary(lambda **kw: [rec]),
            )
            mp.setattr(recommender_module, "persist_recommendations", lambda recs, **kw: len(recs))
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()

        assert len(captured_calls) == 1
        passed = captured_calls[0]
        assert passed.get("candidate_name") == _CANDIDATE_NAME, (
            f"Expected explain_recommendation to receive the RESOLVED candidate name "
            f"{_CANDIDATE_NAME!r}, got {passed.get('candidate_name')!r}."
        )
        assert passed.get("sibling_name") == _SIBLING_NAME, (
            f"Expected explain_recommendation to receive the RESOLVED sibling name "
            f"{_SIBLING_NAME!r}, got {passed.get('sibling_name')!r}."
        )
        # Original identity fields must survive untouched.
        assert passed["candidate_id"] == _CANDIDATE_HASH
        assert passed["sibling_id"] == _SIBLING_HASH

    def test_persisted_raw_response_carries_resolved_names(self, isolated_db):
        """AC-4 dependency: the reuse/material-change check needs
        candidate_name/sibling_name to be present in the PERSISTED
        raw_response (not just the transient dict passed to
        explain_recommendation) so a rename since the prior night can be
        detected. The enrichment must mutate the SAME dict object that flows
        into persist_recommendations, not a throwaway copy."""
        import app as app_module

        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME, _SIBLING_HASH: _SIBLING_NAME})
        rec = _sample_rec()
        captured_persisted: list[dict] = []

        def _fake_persist(recs, **kw):
            captured_persisted.extend(recs)
            return len(recs)

        with pytest.MonkeyPatch.context() as mp:
            import advisors.retirement_explainer as explainer_module
            import advisors.retirement_recommender as recommender_module

            mp.setattr(
                recommender_module,
                "build_recommendations_with_summary",
                with_summary(lambda **kw: [rec]),
            )
            mp.setattr(recommender_module, "persist_recommendations", _fake_persist)
            mp.setattr(explainer_module, "explain_recommendation", lambda r: "explanation text")

            app_module._retirement_recommender_tick_worker()

        assert len(captured_persisted) == 1
        persisted = captured_persisted[0]
        assert persisted.get("candidate_name") == _CANDIDATE_NAME, (
            "candidate_name must be present in the dict handed to "
            "persist_recommendations, not stripped after the explainer call."
        )
        assert persisted.get("sibling_name") == _SIBLING_NAME

    def test_falls_back_to_raw_candidate_id_when_unresolvable(self, isolated_db):
        import app as app_module

        _seed_named_roster({_SIBLING_HASH: _SIBLING_NAME})  # candidate absent from bot_state
        rec = _sample_rec()
        captured_calls: list[dict] = []

        def _fake_explain(r):
            captured_calls.append(dict(r))
            return "x"

        with pytest.MonkeyPatch.context() as mp:
            import advisors.retirement_explainer as explainer_module
            import advisors.retirement_recommender as recommender_module

            mp.setattr(
                recommender_module,
                "build_recommendations_with_summary",
                with_summary(lambda **kw: [rec]),
            )
            mp.setattr(recommender_module, "persist_recommendations", lambda recs, **kw: len(recs))
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()

        assert captured_calls[0].get("candidate_name") == _CANDIDATE_HASH, (
            "An unresolvable candidate must fall back to its own raw id as the "
            "'name' -- never None, never a crash."
        )

    def test_falls_back_to_raw_sibling_id_when_unresolvable(self, isolated_db):
        import app as app_module

        _seed_named_roster({_CANDIDATE_HASH: _CANDIDATE_NAME})  # sibling absent
        rec = _sample_rec()
        captured_calls: list[dict] = []

        def _fake_explain(r):
            captured_calls.append(dict(r))
            return "x"

        with pytest.MonkeyPatch.context() as mp:
            import advisors.retirement_explainer as explainer_module
            import advisors.retirement_recommender as recommender_module

            mp.setattr(
                recommender_module,
                "build_recommendations_with_summary",
                with_summary(lambda **kw: [rec]),
            )
            mp.setattr(recommender_module, "persist_recommendations", lambda recs, **kw: len(recs))
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()

        assert captured_calls[0].get("sibling_name") == _SIBLING_HASH

    def test_bot_state_load_failure_still_explains_with_hash_fallback(
        self, isolated_db, monkeypatch
    ):
        """Edge case: bot_state load failure during the tick -> degrade to
        hashes, never abort the rec (the explainer must still run -- a
        name-resolution failure is not grounds to skip the explanation
        entirely)."""
        import app as app_module
        import database as db_module

        rec = _sample_rec()
        captured_calls: list[dict] = []

        def _raise_load_state(*a, **kw):
            raise RuntimeError("simulated load_state failure")

        def _fake_explain(r):
            captured_calls.append(dict(r))
            return "x"

        monkeypatch.setattr(db_module, "load_state", _raise_load_state)

        with pytest.MonkeyPatch.context() as mp:
            import advisors.retirement_explainer as explainer_module
            import advisors.retirement_recommender as recommender_module

            mp.setattr(
                recommender_module,
                "build_recommendations_with_summary",
                with_summary(lambda **kw: [rec]),
            )
            mp.setattr(recommender_module, "persist_recommendations", lambda recs, **kw: len(recs))
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            try:
                app_module._retirement_recommender_tick_worker()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"_retirement_recommender_tick_worker must never propagate a "
                    f"bot_state load failure: {type(exc).__name__}: {exc}"
                )

        assert len(captured_calls) == 1, (
            "The explainer must still run despite the name-resolution failure."
        )
        assert captured_calls[0].get("candidate_name") == _CANDIDATE_HASH
        assert captured_calls[0].get("sibling_name") == _SIBLING_HASH

    def test_names_are_always_strings_never_none(self, isolated_db):
        """Adversarial non-vacuity guard: whatever the resolution outcome,
        candidate_name/sibling_name must be a str -- never a bare None that
        would corrupt the JSON embedded in the explainer's prompt or a
        future material-change comparison."""
        import app as app_module
        import database as db_module

        db_module.save_state({})  # both candidate and sibling absent
        rec = _sample_rec()
        captured_calls: list[dict] = []

        def _fake_explain(r):
            captured_calls.append(dict(r))
            return "x"

        with pytest.MonkeyPatch.context() as mp:
            import advisors.retirement_explainer as explainer_module
            import advisors.retirement_recommender as recommender_module

            mp.setattr(
                recommender_module,
                "build_recommendations_with_summary",
                with_summary(lambda **kw: [rec]),
            )
            mp.setattr(recommender_module, "persist_recommendations", lambda recs, **kw: len(recs))
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()

        assert isinstance(captured_calls[0].get("candidate_name"), str)
        assert isinstance(captured_calls[0].get("sibling_name"), str)

    def test_multiple_recs_each_get_their_own_resolved_names(self, isolated_db):
        """Non-vacuity: enrichment must be per-rec, not a single value bled
        across every recommendation in the batch."""
        import app as app_module

        other_candidate, other_name = "cand-enrich-other", "Other Candidate"
        other_sibling, other_sibling_name = "sib-enrich-other", "Other Sibling"
        _seed_named_roster(
            {
                _CANDIDATE_HASH: _CANDIDATE_NAME,
                _SIBLING_HASH: _SIBLING_NAME,
                other_candidate: other_name,
                other_sibling: other_sibling_name,
            }
        )
        recs = [_sample_rec(), _sample_rec(candidate_id=other_candidate, sibling_id=other_sibling)]
        captured_calls: list[dict] = []

        def _fake_explain(r):
            captured_calls.append(dict(r))
            return "x"

        with pytest.MonkeyPatch.context() as mp:
            import advisors.retirement_explainer as explainer_module
            import advisors.retirement_recommender as recommender_module

            mp.setattr(
                recommender_module,
                "build_recommendations_with_summary",
                with_summary(lambda **kw: recs),
            )
            mp.setattr(recommender_module, "persist_recommendations", lambda r, **kw: len(r))
            mp.setattr(explainer_module, "explain_recommendation", _fake_explain)

            app_module._retirement_recommender_tick_worker()

        assert len(captured_calls) == 2
        by_candidate = {c["candidate_id"]: c for c in captured_calls}
        assert by_candidate[_CANDIDATE_HASH]["candidate_name"] == _CANDIDATE_NAME
        assert by_candidate[other_candidate]["candidate_name"] == other_name


# ---------------------------------------------------------------------------
# The worker with the real explainer, a real client over the HTTP fake: which
# model each explanation request names, and what is stored when none can be asked
# ---------------------------------------------------------------------------


def _run_worker_with_the_real_explainer(fake, recs):
    """Run the tick worker; return the recommendation dicts handed to persistence."""
    import app as app_module

    persisted: list[dict] = []

    def _persist(recs_to_store, **kw):
        persisted.extend(recs_to_store)
        return len(recs_to_store)

    with pytest.MonkeyPatch.context() as mp, served_by(fake):
        import advisors.retirement_recommender as recommender_module

        mp.setattr(
            recommender_module,
            "build_recommendations_with_summary",
            with_summary(lambda **kw: recs),
        )
        mp.setattr(recommender_module, "persist_recommendations", _persist)
        app_module._retirement_recommender_tick_worker()
    return persisted


class TestExplanationRequestThroughTheWorker:
    @pytest.mark.parametrize(
        "page_fixture",
        ["models/page_captured.json", "models/relisted_newer_fable.json"],
        ids=["captured_listing", "listing_with_a_later_fable_model"],
    )
    def test_the_request_names_the_newest_fable_line_model_listed_at_run_time(
        self, isolated_db, unresolved_models, page_fixture
    ):
        page = load_fixture(page_fixture)
        fake = AnthropicHttpFake(pages=[page])
        fake.queue_message(message_body("thinking_text.json"))

        persisted = _run_worker_with_the_real_explainer(fake, [_sample_rec()])

        (request,) = fake.message_requests
        assert request.body["model"] == newest_id("fable", page["data"])
        assert fake.listing_requests, "the model is resolved through the Models API, not a literal"
        assert persisted[0]["explanation"] == text_of(message_body("thinking_text.json")).strip()
        assert fake.misuse == []

    def test_the_suggestion_override_names_exactly_that_id_and_is_never_listed(
        self, isolated_db, monkeypatch
    ):
        override = older_id("fable")
        monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", override)
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", older_id("opus"))
        fake = AnthropicHttpFake.with_captured_page()
        fake.queue_message(message_body("thinking_text.json"))

        persisted = _run_worker_with_the_real_explainer(fake, [_sample_rec()])

        (request,) = fake.message_requests
        assert request.body["model"] == override
        assert fake.listing_requests == []
        assert request.body.get("output_config") == {"effort": "high"}, (
            "an override request is still the request helper's request (explicit effort)"
        )
        assert persisted[0]["explanation"], "the override reply is stored as the explanation"

    def test_with_no_model_available_every_recommendation_is_stored_without_an_explanation(
        self, isolated_db, unresolved_models, caplog
    ):
        fake = AnthropicHttpFake.with_captured_page()
        for _ in range(4):
            fake.queue_listing_outcome(error_outcome("503_unavailable.json"))
        recs = [_sample_rec(), _sample_rec(candidate_id="cand-enrich-other")]

        with caplog.at_level(logging.WARNING):
            persisted = _run_worker_with_the_real_explainer(fake, recs)

        assert len(persisted) == 2, "a failed explanation never drops a recommendation"
        assert all("explanation" in rec and rec["explanation"] is None for rec in persisted)
        assert fake.message_requests == [], "no request is sent for a family that is unresolved"
        reason_logs = [
            r
            for r in caplog.records
            if r.name == "advisors.retirement_explainer"
            and r.levelno >= logging.WARNING
            and "model_unavailable" in r.getMessage()
        ]
        assert reason_logs, "a WARNING or above must name model_unavailable"

    def test_a_truncated_explanation_is_not_stored_while_the_next_recommendation_still_is(
        self, isolated_db
    ):
        fake = AnthropicHttpFake.with_captured_page()
        fake.queue_message(message_body("max_tokens.json"))
        fake.queue_message(message_body("thinking_text.json"))
        recs = [_sample_rec(), _sample_rec(candidate_id="cand-enrich-other")]

        persisted = _run_worker_with_the_real_explainer(fake, recs)

        assert persisted[0]["explanation"] is None, "the truncated reply's partial text is dropped"
        assert persisted[1]["explanation"] == text_of(message_body("thinking_text.json")).strip()
