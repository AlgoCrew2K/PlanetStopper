"""RED tests -- feature-plans/advisor-outage-degrade.md AC-4/AC-5 (route
layer): POST /ai-advisor/strategy-builder/run must surface the engine's
honest `backtest_unavailable` outage signal in its JSON response, distinctly
from a normal "0 passed the gate" result and from a genuine top-level error.

CONTRACT (converged with dg-fe, see .claude/tdd-handoff.md): the route reads
`ProposalRun.backtest_unavailable` / `.backtest_unavailable_count` DIRECTLY
off `run` (same pattern as the existing `run.error`/`run.error_category`
reads) -- it does NOT recount over `run.candidates` (that would silently
read 0 in exactly the outage scenario this cycle exists to catch; see
tests/advisors/test_strategy_builder_engine_degrade.py's crux test for the
engine-level proof of why). Response fields:
  - `backtest_unavailable`: bool
  - `backtest_unavailable_count`: int
  - `backtest_unavailable_notice`: str | None -- non-empty operator-readable
    prose when count > 0, None otherwise (mirrors the existing `mode_notice`/
    `screens_skipped_reason` honest-empty-state pattern).

WHAT IS MOCKED: strategy_builder_engine.propose_strategies is replaced with a
spy returning a caller-constructed ProposalRun -- isolates the route's JSON-
assembly logic from the full generation/gate chain, same pattern as
tests/advisors/test_r1_sb_repair_and_screens.py. No live network, no live
Composer call.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

import model_config
from tests._sb_generation_harness import (
    CAPTURED_MODELS,
    RELISTED_NEWER_FABLE,
    SUGGESTION_ENV_VAR,
    atlas_candidate,
    conforming_diversify_plans,
    error_outcome,
    fake_with_captured_page,
    fake_with_failing_listing,
    fake_with_newer_fable_listing,
    fallback_reply_with_plans,
    install_chain_seams,
    newest_active_record,
    post_strategy_builder_run,
    text_only_reply,
    tool_reply_with_plans,
)


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _empty_gated_batch():
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch

    return GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q)


def _proposal_run(**overrides):
    import advisors.strategy_builder_engine as sbe

    defaults = dict(
        candidates=[],
        gated_batch=_empty_gated_batch(),
        screened_survivors=[],
        observations_written=0,
    )
    defaults.update(overrides)
    return sbe.ProposalRun(**defaults)


def _post(client, **overrides):
    import advisors.strategy_builder_engine as sbe

    run = _proposal_run(**overrides)
    with (
        patch.object(sbe, "propose_strategies", return_value=run) as mock_propose,
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []}
        )
    return resp, mock_propose


# ===========================================================================
# AC-4: outage surfaced honestly, distinct from a normal empty/rejected run.
# ===========================================================================


def test_ac4_route_surfaces_backtest_unavailable_true_and_count_from_proposalrun(client):
    """MUST FAIL pre-fix: the route today has no backtest_unavailable key at
    all. The route must read the flag/count straight off `run` (mocked
    here), not recompute anything from run.candidates."""
    resp, _ = _post(client, backtest_unavailable=True, backtest_unavailable_count=3)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("backtest_unavailable") is True
    assert body.get("backtest_unavailable_count") == 3


def test_ac4_route_notice_is_a_real_non_empty_string_referencing_the_count(client):
    """When backtest_unavailable is True, backtest_unavailable_notice must be
    a genuine explanatory string (not a bare boolean, not fabricated
    boilerplate) -- and it must reference the actual count, not a hardcoded
    number, mirroring the screens_skipped_reason rigor already established
    in this route (test_r1_sb_repair_and_screens.py)."""
    resp, _ = _post(client, backtest_unavailable=True, backtest_unavailable_count=5)

    body = resp.get_json()
    notice = body.get("backtest_unavailable_notice")
    assert isinstance(notice, str) and len(notice) > 10, (
        f"AC-4 GAP: backtest_unavailable_notice is not a genuine explanatory string. "
        f"Got: {notice!r}"
    )
    assert "5" in notice, (
        f"the notice must reference the ACTUAL count (5), not a hardcoded/generic "
        f"figure. Got: {notice!r}"
    )


def test_ac5_route_omits_backtest_unavailable_notice_on_a_healthy_run(client):
    """AC-5 honest empty-state: on a normal run (no outage), the notice must
    be None/absent -- never a fabricated string. backtest_unavailable/_count
    must be the honest False/0 default, not omitted-and-therefore-ambiguous."""
    resp, _ = _post(client, backtest_unavailable=False, backtest_unavailable_count=0)

    body = resp.get_json()
    assert body.get("backtest_unavailable") is False
    assert body.get("backtest_unavailable_count") == 0
    assert body.get("backtest_unavailable_notice") is None


def test_ac5_route_backtest_unavailable_absent_or_false_on_run_error_branch(client):
    """When ProposalRun.error is set (the route's existing early-return error
    branch, app.py ~4819-4836), the outage fields must not fabricate a True/
    notice -- that branch already has its own distinct error surface
    (error/error_category) and must not also claim an outage that wasn't
    necessarily the (engine-level) cause."""
    resp, _ = _post(client, error="Composer API key not configured")

    body = resp.get_json()
    assert not body.get("backtest_unavailable"), (
        "the run.error early-return branch must not fabricate backtest_unavailable=True"
    )
    assert not body.get("backtest_unavailable_notice")


# ===========================================================================
# Self-guard: prove the spy/mock plumbing itself works (candidate_id survives
# through the route unrelated to the outage fields) -- distinguishes "field
# genuinely missing" from "the whole mock harness broke".
# ===========================================================================


def test_self_guard_mocked_proposalrun_reaches_the_route_json(client):
    resp, mock_propose = _post(client, backtest_unavailable=True, backtest_unavailable_count=1)
    assert mock_propose.called, "self-guard failed: propose_strategies was never called"
    assert resp.status_code == 200


# ===========================================================================
# Model naming, attribution and the degraded-run notice, through the real route
# (real propose_strategies and generator; HTTP fake behind the generator's client
# seam; Alpaca, Composer and Atlas replaced at their boundaries).
# ===========================================================================

_MODEL_FAMILY_WORDS = ("opus", "fable", "sonnet", "haiku", "mythos")


def _suggestion_display_name() -> str:
    return newest_active_record("fable", CAPTURED_MODELS)["display_name"]


def _run_route(client, monkeypatch, fake, *, atlas=None) -> dict:
    with install_chain_seams(monkeypatch, fake, atlas=atlas):
        resp = post_strategy_builder_run(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("error") is None, f"the run must not take the error branch: {body}"
    return body


def test_route_run_names_the_later_fable_line_model_in_the_build_plan_request(client, monkeypatch):
    fake = fake_with_newer_fable_listing()
    model_config._reset_registry()
    reply = tool_reply_with_plans(conforming_diversify_plans())
    fake.queue_message(reply)
    body = _run_route(client, monkeypatch, fake)

    expected = newest_active_record("fable", RELISTED_NEWER_FABLE)["id"]
    assert expected != newest_active_record("fable", CAPTURED_MODELS)["id"]
    assert fake.listing_sequences == 1, "the later model is found by listing, not by a constant"
    assert len(fake.message_requests) == 1
    assert fake.message_requests[0].body["model"] == expected
    assert body["provenance"]["generation_model"] == reply["model"]
    assert body["mode_notice"] is None, "built-new plans were produced, so nothing is degraded"


def test_route_run_with_an_override_names_exactly_that_model(client, monkeypatch):
    override = newest_active_record("opus", CAPTURED_MODELS)["id"]
    monkeypatch.setenv(SUGGESTION_ENV_VAR, f"  {override}  ")
    fake = fake_with_captured_page()
    fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
    _run_route(client, monkeypatch, fake)

    assert fake.message_requests[0].body["model"] == override
    assert fake.listing_requests == []


def test_route_run_reports_the_fallback_model_that_supplied_the_plans(client, monkeypatch):
    fake = fake_with_captured_page()
    reply = fallback_reply_with_plans(conforming_diversify_plans())
    fake.queue_message(reply)
    body = _run_route(client, monkeypatch, fake)

    assert reply["model"] != fake.message_requests[0].body["model"]
    assert body["built_new_count"] >= 1, "plans must come from the fallback reply's tool block"
    assert body["provenance"]["generation_model"] == reply["model"]
    assert body["mode_notice"] is None


def test_unresolved_model_run_still_evaluates_atlas_and_names_the_reason_not_a_family(
    client, monkeypatch, unresolved_models
):
    fake = fake_with_failing_listing()
    body = _run_route(client, monkeypatch, fake, atlas=[atlas_candidate()])

    assert fake.message_requests == [], "no model call is made while the family is unresolved"
    assert body["built_new_count"] == 0
    assert body["atlas_count"] == 1, "the Atlas candidate is still evaluated"
    notice = body["mode_notice"]
    assert isinstance(notice, str) and "model_unavailable" in notice
    assert not any(word in notice.lower() for word in _MODEL_FAMILY_WORDS), notice
    assert body["provenance"]["generation_model"] == ""


def test_unresolved_model_run_costs_one_failed_listing_sequence(
    client, monkeypatch, unresolved_models
):
    fake = fake_with_failing_listing()
    _run_route(client, monkeypatch, fake, atlas=[atlas_candidate()])

    assert fake.listing_sequences == 1, "a failed listing is not repeated for each attempt"
    assert fake.message_requests == []


def test_a_resolved_model_answering_404_with_an_unchanged_relisting_is_one_request(
    client, monkeypatch
):
    fake = fake_with_captured_page()
    fake.queue_message(error_outcome("404_not_found.json"))
    fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
    body = _run_route(client, monkeypatch, fake, atlas=[atlas_candidate()])

    assert len(fake.message_requests) == 1, "a 404 whose relisting agrees is not retried"
    assert fake.listing_sequences == 1
    assert body["built_new_count"] == 0
    assert body["atlas_count"] == 1, "the Atlas candidate is still evaluated"
    notice = body["mode_notice"]
    assert isinstance(notice, str) and "model_unavailable" in notice


def _display_name_of(model_id: str) -> str:
    return next(r["display_name"] for r in CAPTURED_MODELS["data"] if r["id"] == model_id)


def test_a_run_whose_resolved_model_answered_404_names_no_model_in_the_response(
    client, monkeypatch
):
    fake = fake_with_captured_page()
    fake.queue_message(error_outcome("404_not_found.json"))
    fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
    body = _run_route(client, monkeypatch, fake, atlas=[atlas_candidate()])

    assert len(fake.message_requests) == 1
    assert fake.listing_sequences == 1
    rejected_model = fake.message_requests[0].body["model"]
    assert body["built_new_count"] == 0
    assert body["atlas_count"] == 1
    assert body["provenance"]["generation_model"] == "", (
        f"no reply arrived, so the run names no model; got {body['provenance']['generation_model']!r}"
    )
    notice = body["mode_notice"]
    assert isinstance(notice, str) and "model_unavailable" in notice
    lowered = notice.lower()
    assert rejected_model.lower() not in lowered, notice
    assert _display_name_of(rejected_model).lower() not in lowered, notice


def test_an_override_the_api_rejects_names_the_variable_after_one_request(client, monkeypatch):
    override = newest_active_record("opus", CAPTURED_MODELS)["id"]
    monkeypatch.setenv(SUGGESTION_ENV_VAR, override)
    fake = fake_with_captured_page()
    fake.queue_message(error_outcome("400_invalid_request.json"))
    fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
    body = _run_route(client, monkeypatch, fake, atlas=[atlas_candidate()])

    assert len(fake.message_requests) == 1
    assert fake.message_requests[0].body["model"] == override
    assert body["built_new_count"] == 0
    assert f"override_request_rejected: {SUGGESTION_ENV_VAR}" in body["mode_notice"]


def test_degraded_run_notice_names_the_resolved_display_name_and_the_reason_never_opus(
    client, monkeypatch
):
    fake = fake_with_captured_page()
    for _ in range(3):
        fake.queue_message(text_only_reply())
    body = _run_route(client, monkeypatch, fake, atlas=[atlas_candidate()])

    assert len(fake.message_requests) == 3
    notice = body["mode_notice"]
    assert _suggestion_display_name() in notice
    assert "no_tool_call" in notice
    assert "opus" not in notice.lower(), "a suggestion-family call is never labelled Opus"
    assert str(body["atlas_count"]) in notice, "the notice states how many Atlas candidates remain"
    assert "degraded" in notice


def test_degraded_run_notice_names_an_override_exactly_as_set(client, monkeypatch):
    marker = "operator-chosen-model-tag"
    monkeypatch.setenv(SUGGESTION_ENV_VAR, marker)
    fake = fake_with_captured_page()
    for _ in range(3):
        fake.queue_message(text_only_reply())
    body = _run_route(client, monkeypatch, fake, atlas=[atlas_candidate()])

    assert marker in body["mode_notice"]
    assert _suggestion_display_name() not in body["mode_notice"]


def test_route_notice_renders_the_refusal_category_from_the_run(client):
    resp, _ = _post(client, generation_reason="refusal: reasoning_extraction")

    notice = resp.get_json()["mode_notice"]
    assert "refusal: reasoning_extraction" in notice
    assert _suggestion_display_name() in notice


def test_route_notice_omits_the_reason_parenthetical_when_the_run_has_none(client):
    resp, _ = _post(client, generation_reason=None)

    notice = resp.get_json()["mode_notice"]
    assert isinstance(notice, str) and _suggestion_display_name() in notice
    assert "None" not in notice
    assert not re.search(r"\(\s*\)", notice), f"no empty parentheses: {notice!r}"


def test_route_has_no_notice_when_built_new_candidates_exist(client):
    import advisors.strategy_builder_engine as sbe

    built_new = sbe.CandidateInfo(
        candidate_id="plan-1",
        tree={"id": "tree"},
        template_id="built-new",
        params={},
    )
    resp, _ = _post(client, candidates=[built_new])

    assert resp.get_json()["mode_notice"] is None
