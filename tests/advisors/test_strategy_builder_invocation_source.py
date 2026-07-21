"""RED tests — F-030: propose_strategies() invocation-source attribution.

Source: docs/audit/confidence-program/running-register.md lines 244-250 —
"F-030 - DEFECT (LOW, governance/observability): 3 production advisory-DB
writes via a direct off-HTTP-route engine call, unattributable to any
production invocation path ... the production advisory DB can be (and was)
written by direct engine-function calls that bypass the HTTP route and ALL
request logging, leaving observations with no reconstructable audit trail
... REMEDIATION: log/audit-trail every propose_strategies invocation at the
engine level (not just the HTTP route) with its caller/source, so
advisory-DB writes are always attributable."

Scope ruling (main, 2026-07-21): AC-5 narrowed to propose_strategies
invocation attribution specifically — NOT a whole-advisory-write-surface
redesign. Other insert_advisor_observation callers already carry
advisor_role/subject_type/subject_id/symphony_id (a partial trail); this
closes the register's SPECIFIC gap (direct-engine-call bypasses attribution).

CONTRACT PINNED (test-writer decision, mirrors the existing R2-1
run_id/evidence_injected additive-traceability precedent at
strategy_builder_engine.py:716-719 — "always present, never a schema
migration, raw_response is a free-form JSON blob"):

  propose_strategies(..., *, invocation_source: str | None = None) -> ProposalRun

  raw_response["invocation_source"] on every persisted advisor_observations
  row:
    - explicit kwarg value, when supplied, verbatim
    - "unattributed-direct-call" (the honest default) when omitted — THIS is
      the exact scenario the register finding describes (a manual
      python -c/REPL/debug invocation with no caller identification)

  Call-site tags (pinned so the route and scheduler are each unambiguously
  distinguishable from a direct call AND from each other):
    - app.py route (POST /ai-advisor/strategy-builder/run):
        "http-route:/ai-advisor/strategy-builder/run"
    - advisors/strategy_builder_scheduler.py (weekly scheduler):
        "weekly-scheduler"
  (Covered by tests/app/test_strategy_builder_c5_route.py and
  tests/advisors/test_builder_scheduler.py respectively — this file covers
  the engine-level default + persistence-threading contract only.)

Design choice: bypasses the real C1/C2/C3 generation pipeline exactly like
tests/advisors/test_strategy_builder_engine_reasoning_provenance.py (same
_generate_candidate_trees/run_backtest/insert_advisor_observation mock
boundary, same helper shape — a self-contained copy per this suite's
per-file-helper convention, not a cross-file import).
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from datetime import date, timedelta
from unittest.mock import MagicMock

from advisors import strategy_builder_engine as sbe
from advisors import symphony_schema


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


def _run_propose_strategies(monkeypatch, *, invocation_source=None, has_key=True):
    """Drive propose_strategies deterministically: fixed 1-candidate generation
    output, mocked backtest, real gate math, mocked persistence — mirrors
    test_strategy_builder_engine_reasoning_provenance.py's helper exactly.
    """
    monkeypatch.setattr(sbe, "_has_composer_key", lambda: has_key)

    gen_mock = MagicMock(return_value=[_make_candidate_info()])
    monkeypatch.setattr(sbe, "_generate_candidate_trees", gen_mock)
    monkeypatch.setattr(sbe, "run_backtest", lambda *a, **k: _make_fake_result())

    insert_mock = MagicMock(return_value=1)
    monkeypatch.setattr(sbe.database, "insert_advisor_observation", insert_mock)

    kwargs = {}
    if invocation_source is not None:
        kwargs["invocation_source"] = invocation_source

    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify,
        universe=["SPY", "QQQ"],
        screen_config=sbe.ScreenConfig(),
        live_returns=[],
        symphony_id="sym-test",
        **kwargs,
    )
    return run, insert_mock


# ===========================================================================
# AC-5: explicit invocation_source is threaded verbatim into every persisted row.
# ===========================================================================


def test_explicit_invocation_source_persisted_verbatim(monkeypatch):
    _run, insert_mock = _run_propose_strategies(
        monkeypatch, invocation_source="http-route:/ai-advisor/strategy-builder/run"
    )
    assert insert_mock.call_count >= 1, (
        "AC-5 precondition failed: database.insert_advisor_observation was never called — "
        "no candidate was persisted, cannot prove invocation_source traceability."
    )
    for _call_args, call_kwargs in insert_mock.call_args_list:
        raw_response = call_kwargs.get("raw_response", {})
        assert (
            raw_response.get("invocation_source") == "http-route:/ai-advisor/strategy-builder/run"
        ), (
            f"AC-5 GAP: persisted raw_response['invocation_source'] "
            f"({raw_response.get('invocation_source')!r}) does not match the caller-supplied "
            "value — every advisory-DB write from this run must carry the SAME "
            "invocation_source so it's attributable back to its caller."
        )


# ===========================================================================
# AC-5 (the register's own scenario): omitted invocation_source -> honest
# "unattributed-direct-call" default — closing the exact gap the finding
# describes (a manual/direct engine call with no caller identification).
# ===========================================================================


def test_omitted_invocation_source_defaults_to_honest_unattributed_marker(monkeypatch):
    _run, insert_mock = _run_propose_strategies(monkeypatch)  # no invocation_source kwarg at all

    assert insert_mock.call_count >= 1, (
        "AC-5 precondition failed: database.insert_advisor_observation was never called."
    )
    for _call_args, call_kwargs in insert_mock.call_args_list:
        raw_response = call_kwargs.get("raw_response", {})
        assert raw_response.get("invocation_source") == "unattributed-direct-call", (
            "AC-5 GAP: a propose_strategies call with NO invocation_source kwarg — the "
            "EXACT scenario the F-030 register finding describes (a direct engine call "
            "bypassing Flask/HTTP logging) — must self-identify as "
            f"'unattributed-direct-call', never silently pass through as None/absent. "
            f"Got {raw_response.get('invocation_source')!r}"
        )


# ===========================================================================
# AC-5: additive, never replaces the existing run_id/evidence_injected keys.
# ===========================================================================


def test_invocation_source_coexists_with_run_id_and_evidence_injected(monkeypatch):
    run, insert_mock = _run_propose_strategies(monkeypatch, invocation_source="weekly-scheduler")
    for _call_args, call_kwargs in insert_mock.call_args_list:
        raw_response = call_kwargs.get("raw_response", {})
        assert raw_response.get("run_id") == run.run_id, (
            "AC-5/AC-6 GAP: adding invocation_source must not disturb the existing "
            f"run_id traceability key. Got raw_response={raw_response!r}"
        )
        assert "evidence_injected" in raw_response, (
            "AC-5/AC-6 GAP: adding invocation_source must not remove the existing "
            f"evidence_injected traceability key. Got raw_response={raw_response!r}"
        )
        assert "invocation_source" in raw_response, (
            f"AC-5 GAP: invocation_source key missing from raw_response={raw_response!r}"
        )


# ===========================================================================
# AC-5/AC-8: no signature-breaking change — invocation_source is keyword-only
# with a None default, matching reasoning_context/reasoning_manifest/run_id's
# existing convention, so every pre-existing caller (that doesn't know about
# it yet) keeps working unmodified.
# ===========================================================================


def test_invocation_source_is_optional_keyword_only_default_none():
    import inspect

    sig = inspect.signature(sbe.propose_strategies)
    assert "invocation_source" in sig.parameters, (
        "AC-5 GAP: propose_strategies has no invocation_source parameter yet."
    )
    param = sig.parameters["invocation_source"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"AC-5 GAP: invocation_source must be keyword-only (matches reasoning_context/"
        f"reasoning_manifest/run_id convention), got kind={param.kind!r}"
    )
    assert param.default is None, (
        f"AC-5 GAP: invocation_source must default to None (the honest-default marker is "
        f"applied INSIDE the function, mirroring reasoning_manifest's "
        f"None -> ai_advisor._EMPTY_MANIFEST pattern), got default={param.default!r}"
    )


# ===========================================================================
# AC-8: no top-level engine-file import — off-execution-path characterization
# unaffected by this additive change (mirrors the sibling reasoning-provenance
# file's equivalent guard).
# ===========================================================================


def test_proposal_run_dataclass_unaffected_by_invocation_source():
    """AC-8 (adversarial, narrow scope): invocation_source is a raw_response
    persistence detail, NOT a ProposalRun field — the dataclass's field set
    must be untouched by this cycle.
    """
    field_names = {f.name for f in dataclass_fields(sbe.ProposalRun)}
    assert "invocation_source" not in field_names, (
        "AC-5 GAP: invocation_source belongs in the persisted raw_response JSON blob "
        f"(matching run_id/evidence_injected's precedent), not as a new ProposalRun "
        f"dataclass field. Got fields={sorted(field_names)!r}"
    )
