"""RED tests — R2-2 AC-6: honest degradation, never fabricated.

Edge cases pinned (feature-plans/advisor-r2-2-logic-changes.md §Edge Cases):
  - Lens cache cold/stale -> per-lens absent/stale reflected honestly (reused
    verbatim from whatever reasoning_manifest the caller passes — the engine
    never overrides or "upgrades" a manifest value).
  - LLM unavailable / malformed output -> clean "no reasoned proposal this run"
    (NO_SURVIVORS_MESSAGE, the existing empty-is-valid contract), provenance
    still present (AC-5), run does not raise (D-1).
  - D-1 sweep: no failure mode may propagate an exception out of either public
    entry point.

Credential-less throughout; run_backtest and _build_client mocked; no live calls.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

from tests.advisors import logic_change_http_support as support

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


@pytest.fixture()
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _patch_common(lce, monkeypatch):
    monkeypatch.setattr(lce, "_has_composer_key", lambda: True)
    monkeypatch.setattr(lce.database, "insert_advisor_observation", MagicMock(return_value=1))


class _RaisingClient:
    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("simulated LLM outage")

    @property
    def messages(self):
        return self._Messages()


class _MalformedBlock:
    type = "tool_use"
    input = "not-even-a-dict"


class _MalformedResponse:
    stop_reason = "tool_use"
    content = [_MalformedBlock()]


class _MalformedResponseClient:
    class _Messages:
        def create(self, **kwargs):
            return _MalformedResponse()

    @property
    def messages(self):
        return self._Messages()


# ===========================================================================
# LLM unavailable / malformed -> clean degrade, never raise, provenance present.
# ===========================================================================


def test_llm_unavailable_degrades_to_no_reasoned_proposal_clean_message__suggest(
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(lce, "_build_client", lambda: _RaisingClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert result.message == lce.NO_SURVIVORS_MESSAGE, (
        f"AC-6 GAP: LLM outage must degrade to the standard empty-run message, "
        f"got {result.message!r}"
    )
    assert result.survivors == [], "AC-6 GAP: no survivors expected when the LLM is unavailable."
    assert result.provenance is not None, (
        "AC-5/AC-6 GAP: provenance must still be present even when the LLM call itself failed."
    )


def test_llm_unavailable_degrades_to_no_reasoned_proposal_clean_message__operator(
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(lce, "_build_client", lambda: _RaisingClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.propose_operator_logic_change(
        "sym-1", fixture_tree, objective=objective, change_description="tighten the window"
    )

    assert result.message == lce.NO_SURVIVORS_MESSAGE
    assert result.provenance is not None


def test_llm_malformed_tool_output_degrades_cleanly_through_full_entry_point(
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(lce, "_build_client", lambda: _MalformedResponseClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert result.message == lce.NO_SURVIVORS_MESSAGE
    assert result.provenance is not None


# ===========================================================================
# reasoning_manifest gaps reflected honestly, never fabricated/upgraded.
# ===========================================================================


def test_reasoning_manifest_absent_lenses_reflected_honestly_not_fabricated(
    lce, fixture_tree, monkeypatch
):
    _patch_common(lce, monkeypatch)
    monkeypatch.setattr(
        lce, "run_backtest", MagicMock(side_effect=RuntimeError("no backtest needed"))
    )
    monkeypatch.setattr(lce, "generate_reasoned_logic_candidates", MagicMock(return_value=[]))

    manifest = {
        "tree": "absent",
        "stats": "absent",
        "technicals": "stale",
        "sentiment": "absent",
        "derivatives": "absent",
        "macro": "stale",
        "fundamentals": "absent",
    }
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes(
        "sym-1", fixture_tree, objective, reasoning_manifest=manifest
    )

    assert result.provenance.get("evidence_injected") == manifest, (
        f"AC-6 GAP: the engine must reflect the manifest EXACTLY as passed — no upgrading 'stale'/"
        f"'absent' to 'available'. Got {result.provenance.get('evidence_injected')!r}"
    )


# ===========================================================================
# D-1 sweep: no failure mode raises out of either public entry point.
# ===========================================================================


@pytest.mark.parametrize(
    "failure_setup",
    [
        "llm_raises",
        "llm_malformed",
    ],
)
def test_engine_never_raises_across_failure_modes__suggest(
    lce, fixture_tree, monkeypatch, failure_setup
):
    # NOTE: this sweep intentionally does NOT include "a collaborator breaks its
    # own D-1 contract and raises anyway" scenarios (e.g. forcing run_backtest
    # itself to raise) — composer_backtest_client.run_backtest is independently
    # D-1 (never raises on API/transport errors; advisors/composer_backtest_client.py:9),
    # and the EXISTING pre-R2-2 entry points do not defensively re-wrap it either.
    # Forcing that contract to break would test an unrealistic input and could
    # fail identically against the pre-R2-2 code path — out of R2-2's scope.
    # This sweep covers only the NEW failure surface R2-2 introduces:
    # generate_reasoned_logic_candidates's own D-1 contract (proven directly in
    # test_logic_change_engine_reasoned_generation.py) not being silently
    # re-broken by the orchestrating entry point.
    _patch_common(lce, monkeypatch)
    if failure_setup == "llm_raises":
        monkeypatch.setattr(lce, "_build_client", lambda: _RaisingClient())
    elif failure_setup == "llm_malformed":
        monkeypatch.setattr(lce, "_build_client", lambda: _MalformedResponseClient())

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    try:
        result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)
    except Exception as exc:  # noqa: BLE001 - the test itself asserts this must not happen
        pytest.fail(
            f"D-1 GAP ({failure_setup}): suggest_logic_changes raised {type(exc).__name__}: {exc}"
        )
    assert result is not None


# ===========================================================================
# Reply-level degradation through the real route and engine against the
# Anthropic HTTP fake: a reply without a tool call is retried up to 3 attempts
# in total; a refusal or a truncation stops at once; every empty run says why.
# ===========================================================================

_EMPTY_RUN_PREFIX = "no logic change was generated this run"


@pytest.fixture
def degraded(monkeypatch):
    """Run the route once against a fake that answers with the queued outcomes.

    Returns ``(response_json, fake, observation_write_spy)``.
    """

    def _run(*outcomes, page: str = support.CAPTURED_MODELS_PAGE):
        fake = support.new_fake(page)
        spy = support.install_route_world(monkeypatch, fake)
        for outcome in outcomes:
            fake.queue_message(outcome)
        data = support.evaluate_via_route()
        return data, fake, spy

    return _run


def test_three_text_only_replies_send_three_requests_then_report_no_tool_call(degraded):
    text_only = support.load_fixture(support.TEXT_ONLY_REPLY)

    data, fake, spy = degraded(text_only, text_only, text_only)

    assert len(fake.message_requests) == 3, "the attempt bound is 3 requests in total"
    assert fake.misuse == [], "a fourth request would have found no scripted reply"
    assert data["survivors"] == 0
    assert "no_tool_call" in data["message"], f"message must name the reason: {data!r}"
    assert set(data["provenance"]) == {"generation_model", "mode", "evidence_injected", "run_id"}
    assert spy.call_count == 0, "nothing is persisted when no edit was generated"
    assert support.all_proposals(data) == []


def test_each_attempt_is_a_fresh_user_turn_with_the_same_prompt(degraded):
    text_only = support.load_fixture(support.TEXT_ONLY_REPLY)

    _data, fake, _spy = degraded(text_only, text_only, text_only)

    bodies = [r.body for r in fake.message_requests]
    assert len(bodies) == 3, "self-guard: the three attempts must have been sent"
    assert all(b["messages"][-1]["role"] == "user" for b in bodies), (
        "a retry must not end on an assistant turn (a prefill is rejected by models that think)"
    )
    assert len({b["messages"][0]["content"] for b in bodies}) == 1


def test_second_attempt_tool_reply_is_used(degraded):
    edit = support.real_edit()

    data, fake, _spy = degraded(
        support.load_fixture(support.TEXT_ONLY_REPLY), support.reply_with_tool_edit(edit)
    )

    assert len(fake.message_requests) == 2
    assert [p["tweak_param_key"] for p in support.all_proposals(data)] == [edit["param_key"]]
    assert "no_tool_call" not in data["message"]


def test_third_attempt_tool_reply_is_used(degraded):
    edit = support.real_edit()
    text_only = support.load_fixture(support.TEXT_ONLY_REPLY)

    data, fake, _spy = degraded(text_only, text_only, support.reply_with_tool_edit(edit))

    assert len(fake.message_requests) == 3
    assert [p["tweak_param_key"] for p in support.all_proposals(data)] == [edit["param_key"]]


@pytest.mark.parametrize(
    ("reply_path", "expected_text", "absent_text"),
    [
        (support.REFUSAL_REASONING_EXTRACTION_REPLY, "refusal: reasoning_extraction", None),
        (support.REFUSAL_NULL_CATEGORY_REPLY, "refusal", "refusal:"),
    ],
)
def test_refusal_stops_after_one_request_and_names_the_category_when_present(
    degraded, reply_path, expected_text, absent_text
):
    refusal = support.load_fixture(reply_path)

    data, fake, spy = degraded(refusal, refusal, refusal)

    assert len(fake.message_requests) == 1, "the same request is not sent again after a refusal"
    assert expected_text in data["message"], f"message must name the reason: {data!r}"
    if absent_text is not None:
        assert absent_text not in data["message"], "a null category adds no category text"
    assert data["survivors"] == 0
    assert spy.call_count == 0
    assert data["provenance"]["generation_model"] == refusal["model"], (
        "a refused run still reports the model that refused"
    )


def test_max_tokens_stops_after_one_request_and_persists_nothing(degraded):
    truncated = support.load_fixture(support.MAX_TOKENS_REPLY)

    data, fake, spy = degraded(truncated, truncated, truncated)

    assert len(fake.message_requests) == 1, "a truncated reply is not retried at this site"
    assert "max_tokens" in data["message"], f"message must name the reason: {data!r}"
    assert spy.call_count == 0
    assert support.all_proposals(data) == []


def test_refusal_after_partial_tool_input_discards_the_partial_edit(degraded):
    partial = support.refusal_after_partial_tool_reply(support.real_edit())

    data, _fake, spy = degraded(partial)

    assert support.all_proposals(data) == [], (
        "the edit that arrived before the refusal must not be evaluated"
    )
    assert spy.call_count == 0
    assert "refusal" in data["message"]


def test_override_that_the_api_does_not_know_is_reported_by_variable_name_without_retry(
    degraded, monkeypatch
):
    override = support.newest_model_id(support.models_page(), "opus")
    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", override)

    data, fake, spy = degraded(support.load_fixture("errors/404_not_found.json"))

    assert len(fake.message_requests) == 1, "an override is never swapped for the newest model"
    assert "ADVISOR_SUGGESTION_MODEL" in data["message"]
    assert "override_model_not_found" in data["message"]
    assert spy.call_count == 0
    assert fake.listing_requests == [], "an override is never listed or validated"


# ---------------------------------------------------------------------------
# both public entry points: every empty run says why, in one format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", sorted(support.ENTRY_RUNNERS))
def test_unresolved_family_empty_run_message_has_the_reason_format(
    entry, monkeypatch, unresolved_models
):
    fake = support.new_fake()
    support.queue_failing_listings(fake)

    result = support.ENTRY_RUNNERS[entry](fake, monkeypatch)

    assert result.message == f"{_EMPTY_RUN_PREFIX} (model_unavailable)"
    assert fake.message_requests == []
    assert result.provenance["generation_model"] == ""


@pytest.mark.parametrize("entry", sorted(support.ENTRY_RUNNERS))
def test_no_tool_call_empty_run_message_has_the_reason_format(entry, monkeypatch):
    fake = support.new_fake()
    text_only = support.load_fixture(support.TEXT_ONLY_REPLY)
    for _ in range(3):
        fake.queue_message(text_only)

    result = support.ENTRY_RUNNERS[entry](fake, monkeypatch)

    assert result.message == f"{_EMPTY_RUN_PREFIX} (no_tool_call)"
    assert len(fake.message_requests) == 3


@pytest.mark.parametrize("entry", sorted(support.ENTRY_RUNNERS))
def test_refusal_empty_run_message_names_the_category(entry, monkeypatch):
    fake = support.new_fake()
    fake.queue_message(support.load_fixture(support.REFUSAL_REASONING_EXTRACTION_REPLY))

    result = support.ENTRY_RUNNERS[entry](fake, monkeypatch)

    assert result.message == f"{_EMPTY_RUN_PREFIX} (refusal: reasoning_extraction)"


@pytest.mark.parametrize("entry", sorted(support.ENTRY_RUNNERS))
def test_a_generated_edit_that_the_gate_does_not_pass_keeps_the_gate_message(entry, monkeypatch):
    fake = support.new_fake()
    fake.queue_message(support.reply_with_tool_edit(support.real_edit()))

    result = support.ENTRY_RUNNERS[entry](fake, monkeypatch)

    assert result.proposals, "self-guard: the reply must have produced a proposal to gate"
    assert _EMPTY_RUN_PREFIX not in result.message, (
        "an edit was generated; the empty-run reason format is for runs that generated nothing"
    )


# ---------------------------------------------------------------------------
# security: failure detail never reaches the response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_fixture",
    [
        "errors/400_invalid_request.json",
        "errors/401_authentication.json",
        "errors/403_permission.json",
        "errors/429_rate_limit.json",
        "errors/500_api_error.json",
        "errors/529_overloaded.json",
    ],
)
def test_security_api_error_text_is_never_echoed_in_the_response(degraded, error_fixture):
    error = support.load_fixture(error_fixture)

    data, _fake, spy = degraded(error)

    assert data["survivors"] == 0
    assert spy.call_count == 0
    leaked = error["body"]["error"]["message"]
    assert leaked not in json.dumps(data), "the API error message must stay in the server log"
    assert error["body"].get("request_id", "request-id-absent") not in json.dumps(data)
