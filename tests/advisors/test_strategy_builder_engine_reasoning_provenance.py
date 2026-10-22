"""RED tests — R2-1 AC-4/AC-6/AC-8: ProposalRun.run_id / .provenance + persistence
traceability + gate-untouched characterization + import-guard.

Contract pinned (per r2-engine, confirmed via SendMessage before this file was
written):

  propose_strategies(..., *, reasoning_context: str | None = None,
                      reasoning_manifest: dict | None = None) -> ProposalRun

  ProposalRun gains:
    run_id: str        — uuid4 (or caller-supplied override), minted on the
                          FIRST line of propose_strategies, present on EVERY
                          return path of one call (including both the
                          no-Composer-key and top-level-exception early
                          returns).
    provenance: dict    — a REAL 4-key dict on EVERY path, NEVER None, NEVER
                          `{}`: {"generation_model": <raw model_config
                          accessor value, read at call time>, "mode":
                          "build-new", "evidence_injected": <reasoning_manifest,
                          or ai_advisor._EMPTY_MANIFEST when reasoning_manifest
                          is None>, "run_id": <same value as ProposalRun.run_id>}.
                          FINAL RULING (superseded an earlier "absent on
                          run.error" draft of this contract — see the
                          reconciliation thread with team-lead + r2-engine):
                          run_id/generation_model/mode are cheap, non-fabricated
                          facts about the CALL ITSELF (not a claim that
                          generation succeeded), so they are never nulled out.
                          The entire honesty burden is carried by
                          evidence_injected's per-source absent/present/stale
                          values, which are already honest by construction —
                          nulling the whole object on an error path would
                          itself be dishonest if the caller (the route) had
                          already built real evidence via build_reasoning_context
                          BEFORE the Composer-key check ran.

  _persist_survivor / _persist_rejected gain run_id/evidence_injected kwargs
  -> additive raw_response["run_id"], raw_response["evidence_injected"] keys
  on every persisted advisor_observations row (AC-6 traceability).

Design choice: this file bypasses the real C1/C2/C3 generation pipeline
(build_plan_generator, universe_provider, plan_tree_compiler are ALL mocked
out at the _generate_candidate_trees boundary) — that pipeline's reasoning_context
THREADING is proven separately in test_build_plan_generator_reasoning_context.py.
This file proves propose_strategies' OWN new behavior: run_id minting,
provenance stamping, and persistence traceability. run_backtest is mocked
(zero live Composer calls); the REAL backtest_gate_engine.evaluate_candidate_batch
runs unmocked (per project hard rule: math engine never mocked).

Credential-less (AC-7): _has_composer_key is monkeypatched explicitly in every
test (True to reach generation, False for the no-key path) — never relies on
real .env credentials being present or absent.
"""

from __future__ import annotations

import ast
import inspect
import logging
import pathlib
import uuid
from dataclasses import fields as dataclass_fields
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import ai_advisor
import model_config
from advisors import strategy_builder_engine as sbe
from advisors import symphony_schema
from advisors.backtest_gate_engine import evaluate_candidate_batch
from tests._sb_generation_harness import (
    CAPTURED_MODELS,
    atlas_candidate,
    conforming_diversify_plans,
    error_outcome,
    fake_with_captured_page,
    fake_with_failing_listing,
    fallback_reply_with_plans,
    install_chain_seams,
    max_tokens_reply,
    newest_active_record,
    refusal_reply,
    text_only_reply,
    tool_reply_with_plans,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The exact current parameter set of the FDR/PBO/SPY gate — captured directly
# from the live signature at c0cacd4790d53e0fca69bdf654d38e6b396b5660 (BEFORE
# any R2-1 change). See AC-8: the gate is byte-unchanged by R2-1.
_GATE_SIGNATURE_BEFORE_R2_1 = frozenset(
    {"candidates", "incumbent_oos_alpha", "default_oos_alpha", "spy_returns_fn"}
)


# ---------------------------------------------------------------------------
# Shared deterministic backtest stand-ins (mirrors test_strategy_builder_engine.py's
# _make_fake_result helper — self-contained copy, not a cross-file import, per
# this suite's per-file-helper convention).
# ---------------------------------------------------------------------------


def _make_fake_result(n_days: int = 100, base_return: float = 0.001):
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = base_return * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08}, data_warnings=[], daily_returns=returns
    )


def _make_candidate_info(candidate_id: str = "cand-1") -> sbe.CandidateInfo:
    tree = symphony_schema.make_root(
        "Test", "daily", [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])]
    )
    return sbe.CandidateInfo(
        candidate_id=candidate_id,
        tree=tree,
        template_id="built-new",
        params={
            "plan_id": candidate_id,
            "name": "P1",
            "objective": "diversify",
            "provenance": "built-new",
        },
        metrics={},
        backtest_error=None,
    )


def _run_propose_strategies(
    monkeypatch, *, reasoning_context=None, reasoning_manifest=None, run_id=None, has_key=True
):
    """Drive propose_strategies deterministically: fixed 1-candidate generation
    output, mocked backtest, real gate math, mocked persistence."""
    monkeypatch.setattr(sbe, "_has_composer_key", lambda: has_key)

    gen_mock = MagicMock(return_value=[_make_candidate_info()])
    monkeypatch.setattr(sbe, "_generate_candidate_trees", gen_mock)
    monkeypatch.setattr(sbe, "run_backtest", lambda *a, **k: _make_fake_result())

    insert_mock = MagicMock(return_value=1)
    monkeypatch.setattr(sbe.database, "insert_advisor_observation", insert_mock)

    kwargs = {}
    if reasoning_context is not None:
        kwargs["reasoning_context"] = reasoning_context
    if reasoning_manifest is not None:
        kwargs["reasoning_manifest"] = reasoning_manifest
    if run_id is not None:
        kwargs["run_id"] = run_id

    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify,
        universe=["SPY", "QQQ"],
        screen_config=sbe.ScreenConfig(),
        live_returns=[],
        symphony_id="sym-test",
        **kwargs,
    )
    return run, gen_mock, insert_mock


# ===========================================================================
# AC-4: ProposalRun.provenance shape, accessor-driven model.
# ===========================================================================


def test_provenance_shape_and_accessor_driven_model(monkeypatch):
    """AC-4: provenance == {generation_model: <accessor value>, mode: 'build-new',
    evidence_injected: <the passed manifest>}. Model is READ from the accessor
    at call time — proven by monkeypatching it to a marker, mirroring the
    test_r1_attribution_honesty.py technique."""
    # An override is returned verbatim by the model resolver, so a marker proves the value is
    # read at call time from the resolver and never from a literal.
    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-sb-model")
    manifest = {
        "tree": "present",
        "stats": "absent",
        **{
            n: "absent" for n in ("technicals", "sentiment", "derivatives", "macro", "fundamentals")
        },
    }

    run, _gen_mock, _insert_mock = _run_propose_strategies(
        monkeypatch,
        reasoning_context="## OPERATOR CONTEXT\nHOLD SPY\n",
        reasoning_manifest=manifest,
    )

    assert run.provenance is not None, "AC-4 GAP: run.provenance is None on a successful run."
    assert run.provenance.get("generation_model") == "test-marker-sb-model", (
        f"AC-4 GAP: provenance['generation_model'] must reflect the LIVE accessor value "
        f"(monkeypatched to a marker) — never a hardcoded literal. Got {run.provenance!r}"
    )
    assert run.provenance.get("mode") == "build-new", f"AC-4 GAP: provenance={run.provenance!r}"
    assert run.provenance.get("evidence_injected") == manifest, (
        f"AC-4 GAP: provenance['evidence_injected'] must equal the passed reasoning_manifest "
        f"exactly. Got {run.provenance.get('evidence_injected')!r}, expected {manifest!r}"
    )
    assert run.provenance.get("run_id") == run.run_id, (
        f"AC-4/AC-6 GAP: provenance['run_id'] must equal ProposalRun.run_id (the FINAL "
        f"contract nests run_id INSIDE provenance too — a 4-key dict, not 3). "
        f"provenance={run.provenance!r}, run.run_id={run.run_id!r}"
    )


def test_provenance_key_set_is_exactly_four_keys(monkeypatch):
    """AC-4 (adversarial, narrow scope): provenance's key set must be EXACTLY
    {generation_model, mode, evidence_injected, run_id} — never a 3-key dict
    (the superseded draft contract) and never a surprise 5th key."""
    run, _gen_mock, _insert_mock = _run_propose_strategies(
        monkeypatch, reasoning_manifest={"tree": "present"}
    )
    assert set(run.provenance.keys()) == {
        "generation_model",
        "mode",
        "evidence_injected",
        "run_id",
    }, (
        f"AC-4 GAP: provenance key set is {sorted(run.provenance.keys())!r}, expected exactly "
        "{'generation_model', 'mode', 'evidence_injected', 'run_id'}."
    )


def test_provenance_evidence_injected_defaults_to_empty_manifest_when_none(monkeypatch):
    """AC-3/AC-8: reasoning_manifest omitted (from-scratch run) -> evidence_injected
    is NEVER None/absent — it must be the same honest all-absent manifest shape
    ai_advisor.build_reasoning_context uses for a falsy symphony_id
    (ai_advisor._EMPTY_MANIFEST), proving 'no manifest supplied' degrades to an
    honest signal, never a fabricated 'everything available' default."""
    run, _gen_mock, _insert_mock = _run_propose_strategies(monkeypatch)  # no reasoning_manifest

    assert run.provenance is not None, "AC-4 GAP: run.provenance is None on a successful run."
    empty_manifest = getattr(ai_advisor, "_EMPTY_MANIFEST", None)
    assert empty_manifest is not None, (
        "GAP: ai_advisor._EMPTY_MANIFEST does not exist yet — expected per r2-engine's "
        'own stated contract (falsy symphony_id -> ("", _EMPTY_MANIFEST)).'
    )
    assert run.provenance.get("evidence_injected") == empty_manifest, (
        f"AC-3/AC-8 GAP: with reasoning_manifest omitted, evidence_injected must equal "
        f"ai_advisor._EMPTY_MANIFEST exactly (honest all-absent signal). "
        f"Got {run.provenance.get('evidence_injected')!r}, expected {empty_manifest!r}"
    )


# ===========================================================================
# AC-6: run_id minted, stable, and traceable through persistence.
# ===========================================================================


def test_run_id_minted_as_valid_uuid4_when_omitted(monkeypatch):
    """AC-6: run_id omitted -> ProposalRun.run_id is minted and is a
    well-formed UUID4 string — format-only assertion, never a literal
    comparison against a producer-generated value."""
    run, _gen_mock, _insert_mock = _run_propose_strategies(monkeypatch)

    assert isinstance(run.run_id, str) and run.run_id, (
        f"AC-6 GAP: run.run_id must be a non-empty string. Got {run.run_id!r}"
    )
    parsed = uuid.UUID(
        run.run_id
    )  # raises ValueError if not a valid UUID — the test itself fails loudly
    assert parsed.version == 4, f"AC-6 GAP: run_id must be a UUID4, got version={parsed.version}"


def test_run_id_persisted_traceable_to_advisor_observation_raw_response(monkeypatch):
    """AC-6 (the traceability proof): the SAME run_id that lands on
    ProposalRun.run_id must be written into the persisted
    advisor_observations.raw_response of every candidate this run touches —
    proven by capturing database.insert_advisor_observation's call kwargs."""
    manifest = {"tree": "absent", "stats": "absent"}
    run, _gen_mock, insert_mock = _run_propose_strategies(monkeypatch, reasoning_manifest=manifest)

    assert insert_mock.call_count >= 1, (
        "AC-6 precondition failed: database.insert_advisor_observation was never called — "
        "no candidate was persisted, cannot prove traceability."
    )
    for _call_args, call_kwargs in insert_mock.call_args_list:
        raw_response = call_kwargs.get("raw_response", {})
        assert raw_response.get("run_id") == run.run_id, (
            f"AC-6 GAP: a persisted advisor_observations row's raw_response['run_id'] "
            f"({raw_response.get('run_id')!r}) does not match ProposalRun.run_id "
            f"({run.run_id!r}) — the run cannot be traced back to its provenance."
        )
        assert raw_response.get("evidence_injected") == manifest, (
            f"AC-6 GAP: persisted raw_response['evidence_injected'] must equal the run's "
            f"manifest. Got {raw_response.get('evidence_injected')!r}, expected {manifest!r}"
        )


def test_caller_supplied_run_id_is_honored_and_used_verbatim(monkeypatch):
    """AC-6 (test seam): propose_strategies accepts an explicit run_id kwarg
    (test/caller-supplied) and uses it VERBATIM rather than minting a new one —
    lets tests assert a known id end-to-end without parsing a random UUID."""
    fixed_id = "11111111-1111-4111-8111-111111111111"
    run, _gen_mock, insert_mock = _run_propose_strategies(monkeypatch, run_id=fixed_id)

    assert run.run_id == fixed_id, (
        f"AC-6 GAP: caller-supplied run_id not honored. Got {run.run_id!r}"
    )
    _call_args, call_kwargs = insert_mock.call_args_list[0]
    assert call_kwargs.get("raw_response", {}).get("run_id") == fixed_id, (
        "AC-6 GAP: caller-supplied run_id did not propagate into the persisted raw_response."
    )


# ===========================================================================
# Threading — propose_strategies passes reasoning_context/manifest down to
# _generate_candidate_trees (the C1/C2/C3 entry point).
# ===========================================================================


def test_reasoning_context_threaded_into_generate_candidate_trees_call(monkeypatch):
    """propose_strategies must forward reasoning_context to
    _generate_candidate_trees(objective, universe, reasoning_context=...) —
    proven via the mocked function's call_args, not by re-testing generation
    internals (that's test_build_plan_generator_reasoning_context.py's job)."""
    _run, gen_mock, _insert_mock = _run_propose_strategies(
        monkeypatch, reasoning_context="## OPERATOR CONTEXT\nHOLD SPY\n"
    )
    assert gen_mock.called, "GAP: _generate_candidate_trees was never called."
    _call_args, call_kwargs = gen_mock.call_args
    assert call_kwargs.get("reasoning_context") == "## OPERATOR CONTEXT\nHOLD SPY\n", (
        f"GAP: propose_strategies did not thread reasoning_context into "
        f"_generate_candidate_trees. call_kwargs={call_kwargs!r}"
    )


# ===========================================================================
# AC-8: gate math is BYTE-unchanged — a tripwire, not a re-test of gate math.
# ===========================================================================


def test_gate_signature_unchanged_reasoning_context_never_leaks_into_gate_math():
    """AC-8 characterization/tripwire: evaluate_candidate_batch's parameter set
    must be EXACTLY {candidates, incumbent_oos_alpha, default_oos_alpha,
    spy_returns_fn} — pinned from the live signature before any R2-1 change.
    A future implementer threading reasoning_context/run_id into the gate
    (e.g. to 'help' the FDR correction) would silently break the R1 gate-
    parity guarantee this test exists to catch."""
    actual = frozenset(inspect.signature(evaluate_candidate_batch).parameters.keys())
    assert actual == _GATE_SIGNATURE_BEFORE_R2_1, (
        f"AC-8 GAP: evaluate_candidate_batch's signature changed from "
        f"{sorted(_GATE_SIGNATURE_BEFORE_R2_1)} to {sorted(actual)} — the FDR/PBO/SPY gate "
        "must stay byte-unchanged by R2-1 (R1 parity)."
    )


def test_proposal_run_dataclass_gains_only_run_id_and_provenance():
    """AC-8 (adversarial, narrow scope): ProposalRun's field set must be the
    PRE-R2-1 8 fields PLUS exactly {run_id, provenance, generation_reason} — never a surprise
    extra field that would indicate scope creep beyond the plan's contract."""
    pre_r2_1_fields = {
        "candidates",
        "gated_batch",
        "screened_survivors",
        "observations_written",
        "error",
        "error_category",
        "backtest_unavailable",
        "backtest_unavailable_count",
    }
    actual = {f.name for f in dataclass_fields(sbe.ProposalRun)}
    expected = pre_r2_1_fields | {"run_id", "provenance", "generation_reason"}
    assert actual == expected, (
        f"AC-8 GAP: ProposalRun field set is {sorted(actual)}, expected exactly "
        f"{sorted(expected)} (pre-R2-1 8 fields + run_id + provenance + generation_reason, nothing else)."
    )


# ===========================================================================
# Import-guard: off-execution-path preserved (no alpha_bot_execution/math_engine).
# ===========================================================================


def _module_top_level_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


@pytest.mark.parametrize(
    "module_path",
    [
        _REPO_ROOT / "advisors" / "strategy_builder_engine.py",
        _REPO_ROOT / "ai_advisor.py",
    ],
)
def test_no_top_level_alpha_bot_execution_or_math_engine_import(module_path):
    """AC-8: neither strategy_builder_engine.py nor ai_advisor.py (the two
    modules R2-1 touches — build_reasoning_context lives on ai_advisor per
    r2-engine's choice) may import alpha_bot_execution or math_engine at
    module (top) scope. A top-level import would couple the 1-minute live
    execution path to advisory reasoning-context assembly."""
    names = _module_top_level_imports(module_path)
    for forbidden in ("alpha_bot_execution", "math_engine"):
        assert not any(forbidden in name for name in names), (
            f"AC-8 GAP: {module_path.name} imports {forbidden!r} at module scope "
            f"(top-level imports found: {sorted(names)!r}) — this must be lazy "
            "(inside a function body) or absent entirely, per the off-execution-path "
            "invariant."
        )


# ===========================================================================
# AC-7: credential-less — no-key path still carries real (never fabricated,
# never nulled) provenance. FINAL ruling: provenance is a real dict on every
# path; only evidence_injected's per-source values carry the honesty signal.
# ===========================================================================


def test_no_composer_key_still_populates_real_provenance_never_none(monkeypatch):
    """AC-7 (FINAL contract — supersedes an earlier draft of this test that
    asserted provenance is None on this path): even on the no-Composer-key
    early-return, provenance is a REAL 4-key dict — generation_model/mode/
    run_id are cheap non-fabricated facts about the call itself, never nulled.
    evidence_injected reflects whatever reasoning_manifest was actually passed
    in (built by build_reasoning_context BEFORE the Composer-key check ever
    runs) — nulling it here would itself be dishonest if the caller had real
    evidence. Only evidence_injected's per-source absent/present/stale values
    carry the honesty signal; the envelope around them never disappears."""
    monkeypatch.setattr(sbe, "_has_composer_key", lambda: False)
    # An override is returned verbatim by the model resolver, so a marker proves the value is
    # read at call time from the resolver and never from a literal.
    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-sb-model")
    manifest = {"tree": "absent", "stats": "absent"}

    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify,
        universe=["SPY"],
        screen_config=sbe.ScreenConfig(),
        live_returns=[],
        symphony_id="sym-test",
        reasoning_manifest=manifest,
    )

    assert run.error, "Test precondition: no-key path must set run.error."
    assert run.provenance is not None, (
        f"GAP: provenance must NEVER be None, even on the no-key error path (FINAL "
        f"contract — supersedes the earlier 'absent on error' draft). Got {run.provenance!r}"
    )
    assert run.provenance.get("generation_model") == "test-marker-sb-model", (
        f"GAP: provenance={run.provenance!r}"
    )
    assert run.provenance.get("mode") == "build-new", f"GAP: provenance={run.provenance!r}"
    assert run.provenance.get("evidence_injected") == manifest, (
        "GAP: evidence_injected must reflect the reasoning_manifest actually passed in "
        "(built before the Composer-key check ran) — nulling it here would be dishonest "
        f"if the caller had real evidence. Got {run.provenance.get('evidence_injected')!r}"
    )
    assert isinstance(run.run_id, str) and run.run_id, (
        f"AC-6 GAP: run_id must still be minted even on the error early-return "
        f"(every return path gets a run_id, per contract). Got {run.run_id!r}"
    )
    uuid.UUID(run.run_id)  # must still be a valid UUID4 string on the error path
    assert run.provenance.get("run_id") == run.run_id, (
        f"AC-4/AC-6 GAP: provenance['run_id'] must equal ProposalRun.run_id even on the "
        f"error path. provenance={run.provenance!r}, run.run_id={run.run_id!r}"
    )


# ===========================================================================
# Model attribution and the generation outcome, through the real generator
# (HTTP fake behind the generator's client seam; Alpaca, Composer and Atlas
# replaced at their boundaries by tests/_sb_generation_harness.py).
# ===========================================================================


def _run_real_generation(monkeypatch, fake, *, atlas=None, **kwargs):
    stack = install_chain_seams(monkeypatch, fake, atlas=atlas)
    with stack:
        return sbe.propose_strategies(
            objective=sbe.Objective.diversify,
            universe=[],
            screen_config=sbe.ScreenConfig(),
            live_returns=[],
            symphony_id="",
            community_candidates=list(atlas or []),
            **kwargs,
        )


def _captured_newest_suggestion_id() -> str:
    return newest_active_record("fable", CAPTURED_MODELS)["id"]


def test_proposal_run_carries_a_generation_reason_defaulting_to_none():
    fields = {f.name: f for f in dataclass_fields(sbe.ProposalRun)}
    assert "generation_reason" in fields, "ProposalRun must gain a generation_reason field"
    assert fields["generation_reason"].default is None


def test_successful_generation_names_the_reply_model_and_has_no_reason(monkeypatch):
    fake = fake_with_captured_page()
    reply = tool_reply_with_plans(conforming_diversify_plans())
    fake.queue_message(reply)
    run = _run_real_generation(monkeypatch, fake)

    assert run.error is None
    assert run.generation_reason is None
    assert any(c.template_id == "built-new" for c in run.candidates)
    assert run.provenance["generation_model"] == reply["model"]


def test_provenance_names_the_reply_model_when_nothing_was_resolved_at_run_start(monkeypatch):
    """Nothing is resolved when the run starts (peek is empty); the request resolves the
    family itself, so the model that answered is the only honest value to report."""
    model_config._reset_registry()
    assert model_config.peek(model_config.Knob.SUGGESTION).model_id == ""
    fake = fake_with_captured_page()
    reply = tool_reply_with_plans(conforming_diversify_plans())
    fake.queue_message(reply)
    run = _run_real_generation(monkeypatch, fake)

    assert run.provenance["generation_model"] == reply["model"]
    assert run.provenance["generation_model"] != ""


def test_fallback_served_reply_supplies_plans_and_provenance_names_the_serving_model(monkeypatch):
    fake = fake_with_captured_page()
    reply = fallback_reply_with_plans(conforming_diversify_plans())
    fake.queue_message(reply)
    run = _run_real_generation(monkeypatch, fake)

    requested = fake.message_requests[0].body["model"]
    assert reply["model"] != requested
    assert any(c.template_id == "built-new" for c in run.candidates), (
        "plans must come from the tool block of the fallback reply"
    )
    assert run.provenance["generation_model"] == reply["model"]
    assert run.generation_reason is None


@pytest.mark.parametrize(
    ("replies", "expected_reason"),
    [
        pytest.param([text_only_reply] * 3, "no_tool_call", id="no-tool-call-after-3-attempts"),
        pytest.param([max_tokens_reply] * 3, "max_tokens", id="truncated-after-3-attempts"),
        pytest.param([refusal_reply], "refusal: reasoning_extraction", id="refusal-with-category"),
    ],
)
def test_degraded_generation_records_the_reason_and_keeps_atlas_candidates(
    monkeypatch, replies, expected_reason
):
    fake = fake_with_captured_page()
    for make_reply in replies:
        fake.queue_message(make_reply())
    run = _run_real_generation(monkeypatch, fake, atlas=[atlas_candidate()])

    assert run.error is None, "a degraded generation is not a run error"
    assert run.generation_reason == expected_reason
    assert [c.template_id for c in run.candidates] == ["atlas-suggested"]
    assert len(fake.message_requests) == len(replies)


def test_unresolved_family_makes_no_request_and_reports_model_unavailable(
    monkeypatch, unresolved_models
):
    fake = fake_with_failing_listing()
    run = _run_real_generation(monkeypatch, fake, atlas=[atlas_candidate()])

    assert fake.message_requests == []
    assert run.generation_reason == "model_unavailable"
    assert run.provenance["generation_model"] == ""
    assert [c.template_id for c in run.candidates] == ["atlas-suggested"]


def test_unresolved_family_costs_the_run_one_failed_listing_sequence(
    monkeypatch, unresolved_models
):
    fake = fake_with_failing_listing()
    _run_real_generation(monkeypatch, fake, atlas=[atlas_candidate()])

    assert fake.listing_sequences == 1, "a failed listing is not repeated for each attempt"
    assert fake.message_requests == []


def test_a_resolved_model_answering_404_with_an_unchanged_relisting_is_one_request(monkeypatch):
    fake = fake_with_captured_page()
    fake.queue_message(error_outcome("404_not_found.json"))
    fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
    run = _run_real_generation(monkeypatch, fake, atlas=[atlas_candidate()])

    assert len(fake.message_requests) == 1, "a 404 whose relisting agrees is not retried"
    assert fake.listing_sequences == 1
    assert run.error is None
    assert run.generation_reason == "model_unavailable"
    assert [c.template_id for c in run.candidates] == ["atlas-suggested"]


def test_no_composer_key_path_reports_the_resolved_model_and_no_generation_reason(monkeypatch):
    monkeypatch.setattr(sbe, "_has_composer_key", lambda: False)
    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify,
        universe=["SPY"],
        screen_config=sbe.ScreenConfig(),
        live_returns=[],
        symphony_id="",
    )

    assert run.error
    assert run.generation_reason is None
    assert run.provenance["generation_model"] == _captured_newest_suggestion_id()


def test_a_patched_list_returning_generator_still_drives_provenance_from_the_resolver(
    monkeypatch,
):
    """Existing callers and tests replace ``_generate_candidate_trees`` with a callable that
    returns a plain list. That must keep working: no reason, and the model from the resolver."""
    run, gen_mock, _insert_mock = _run_propose_strategies(monkeypatch)

    assert gen_mock.called
    assert run.generation_reason is None
    assert run.provenance["generation_model"] == _captured_newest_suggestion_id()
    # A plain list must not send the run to its outer exception handler.
    assert run.error is None
    assert [c.candidate_id for c in run.candidates] == ["cand-1"]


def test_generator_returning_no_plans_is_logged_at_warning_with_the_reason(monkeypatch, caplog):
    fake = fake_with_captured_page()
    for _ in range(3):
        fake.queue_message(text_only_reply())
    with caplog.at_level(logging.WARNING, logger="advisors.strategy_builder_engine"):
        _run_real_generation(monkeypatch, fake, atlas=[atlas_candidate()])

    records = [
        r
        for r in caplog.records
        if r.name == "advisors.strategy_builder_engine" and r.levelno >= logging.WARNING
    ]
    assert any("no_tool_call" in r.getMessage() for r in records), (
        "a generation that produced no plans must reach the journal at WARNING or above"
    )


def test_unexpected_generation_failure_is_logged_at_warning_with_the_type_name_only(
    monkeypatch, caplog
):
    import advisors.universe_provider as universe

    def boom(*_a, **_k):
        raise RuntimeError("secret-universe-source-detail")

    monkeypatch.setattr(universe, "get_tradeable_set", boom)
    with caplog.at_level(logging.WARNING, logger="advisors.strategy_builder_engine"):
        result = sbe._generate_candidate_trees(sbe.Objective.diversify, [])

    assert result == []
    records = [
        r
        for r in caplog.records
        if r.name == "advisors.strategy_builder_engine" and r.levelno >= logging.WARNING
    ]
    assert any("RuntimeError" in r.getMessage() for r in records)
    assert "secret-universe-source-detail" not in caplog.text, (
        "only the exception type name may be logged, never its message or a traceback"
    )


def test_generate_candidate_trees_keeps_returning_a_list(monkeypatch):
    fake = fake_with_captured_page()
    fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
    with install_chain_seams(monkeypatch, fake):
        result = sbe._generate_candidate_trees(sbe.Objective.diversify, [])

    assert isinstance(result, list)
    assert result and all(isinstance(c, sbe.CandidateInfo) for c in result)
