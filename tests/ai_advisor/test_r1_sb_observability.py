"""RED tests — AC-11 (F5, Gap E): SB run-level mode banner + sanitized route-error cause.

Confirmed by reading app.py:4588-4719 (the SB route) and
strategy_builder_engine.py's ProposalRun dataclass directly before writing:
  - ProposalRun currently has only `error: str | None` — no `error_category`
    field. The route's error branch (app.py:4669) returns a STATIC
    "strategy-builder-error" token regardless of cause — AC-11 wants a real
    cause CATEGORY (still sanitized, never raw exception text, AC-23
    precedent preserved).
  - The route's `_gate_result_to_dict` already reads `info.template_id` per
    candidate (app.py:4689) but computes no run-level `built_new_count`/
    `atlas_count` rollup, and there is no "0 built-new plans (degraded)"
    notice anywhere — a run where Atlas-only candidates populate the result
    (all built-new branches failed) renders as an ordinary success today.

CONTRACT (team-lead's locked field names): `built_new_count`/`atlas_count`
computed route-side from `run.candidates[].template_id ==
"built-new"/"atlas-suggested"`; `ProposalRun.error_category` token.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _proposal_run_with_candidates(sbe, template_ids: list[str]):
    """A minimal real ProposalRun with candidates carrying the given
    template_id provenance mix — isolates the route's rollup-computation
    logic from the full generation/backtest/gate chain."""
    from advisors.backtest_gate_engine import GatedBatch
    from advisors.strategy_builder_engine import CandidateInfo

    candidates = [
        CandidateInfo(candidate_id=f"cand-{i}", tree={}, template_id=tid, params={})
        for i, tid in enumerate(template_ids)
    ]
    return sbe.ProposalRun(
        candidates=candidates,
        gated_batch=GatedBatch(results=[], survivors=[], n_candidates=len(candidates), fdr_q=0.05),
        screened_survivors=[],
        observations_written=0,
    )


# ===========================================================================
# built_new_count / atlas_count rollup.
# ===========================================================================


def test_ac11_sb_route_reports_built_new_and_atlas_counts(client):
    """MUST FAIL pre-fix: the route response carries no built_new_count/
    atlas_count fields at all today."""
    import advisors.strategy_builder_engine as sbe

    run = _proposal_run_with_candidates(
        sbe, ["built-new", "built-new", "atlas-suggested", "built-new"]
    )
    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("built_new_count") == 3, (
        f"AC-11 GAP: expected built_new_count=3 (derived from the fixture's "
        f"template_id mix, never hardcoded), got {body.get('built_new_count')!r}. "
        f"Full response: {body}"
    )
    assert body.get("atlas_count") == 1, (
        f"AC-11 GAP: expected atlas_count=1, got {body.get('atlas_count')!r}. "
        f"Full response: {body}"
    )


def test_ac11_sb_route_degraded_notice_when_zero_built_new_plans(client):
    """MUST FAIL pre-fix: an Atlas-only run (all built-new branches failed)
    renders as an ordinary success today — no run-level signal distinguishes
    it from a healthy run. The operator cannot tell 'Opus produced nothing'
    from 'Opus produced everything you see'."""
    import advisors.strategy_builder_engine as sbe

    run = _proposal_run_with_candidates(sbe, ["atlas-suggested", "atlas-suggested"])
    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    body = resp.get_json()
    assert body.get("built_new_count") == 0
    rendered_text = " ".join(str(v) for v in body.values())
    assert "0 plans" in rendered_text or "degraded" in rendered_text.lower(), (
        f"AC-11 GAP: a zero-built-new-plans run does not carry an explicit "
        f"degraded notice. Response: {body}"
    )


def test_ac11_sb_route_healthy_run_carries_no_false_degraded_notice(client):
    """Non-regression: a genuinely healthy run (built_new_count > 0) must NOT
    carry the degraded notice — a fix that always renders the notice would
    pass the test above vacuously."""
    import advisors.strategy_builder_engine as sbe

    run = _proposal_run_with_candidates(sbe, ["built-new", "built-new"])
    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    body = resp.get_json()
    assert body.get("built_new_count") == 2
    rendered_text = " ".join(str(v) for v in body.values())
    assert "degraded" not in rendered_text.lower(), (
        f"AC-11 GAP: a healthy run (built_new_count=2) falsely carries the "
        f"degraded notice. Response: {body}"
    )


# ===========================================================================
# Route-error cause category (ProposalRun.error_category).
# ===========================================================================


def test_ac11_proposal_run_has_error_category_field():
    """MUST FAIL pre-fix: ProposalRun has no error_category field today (only
    `error: str | None`, which per AC-23/D-1 precedent must never be echoed
    to the client verbatim — a category token is the sanitized alternative)."""
    import advisors.strategy_builder_engine as sbe
    from advisors.backtest_gate_engine import GatedBatch

    run = sbe.ProposalRun(
        candidates=[],
        gated_batch=GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=0.05),
        screened_survivors=[],
        observations_written=0,
        error="ConnectionError: could not reach https://internal-secret-host/api?key=sk-ant-abc123",
    )
    assert hasattr(run, "error_category"), (
        "AC-11 GAP: ProposalRun has no error_category field — the route "
        "cannot surface a sanitized cause without it."
    )


def test_ac11_sb_route_error_response_never_echoes_raw_exception_text(client):
    """Adversarial (mirrors AC-23 precedent): the route-error branch must
    surface a CATEGORY token, never the raw exception text — which may
    contain credentials, internal hostnames, or paths. A candidate-shaped
    secret string is injected via ProposalRun.error to prove it never leaks."""
    import advisors.strategy_builder_engine as sbe
    from advisors.backtest_gate_engine import GatedBatch

    _SECRET_MARKER = "sk-ant-should-never-leak-abc123"
    run_kwargs = dict(
        candidates=[],
        gated_batch=GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=0.05),
        screened_survivors=[],
        observations_written=0,
        error=f"ConnectionError: could not reach https://internal-host/api?key={_SECRET_MARKER}",
    )
    # error_category doesn't exist on ProposalRun pre-fix (see the field-
    # existence test above) — only pass it once the fix lands, so this test
    # can run against BOTH the pre-fix and post-fix dataclass shape.
    if "error_category" in getattr(sbe.ProposalRun, "__dataclass_fields__", {}):
        run_kwargs["error_category"] = "ConnectionError"
    run = sbe.ProposalRun(**run_kwargs)

    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = client.post("/ai-advisor/strategy-builder/run", json={"objective": "diversify", "universe": []})

    body = resp.get_json()
    rendered_text = " ".join(str(v) for v in body.values())
    assert _SECRET_MARKER not in rendered_text, (
        f"AC-11 SECURITY GAP: the raw error text (containing a credential-"
        f"shaped secret) leaked into the route response. Response: {body}"
    )
    assert rendered_text != "strategy-builder-error" or "error_category" in body, (
        f"AC-11 GAP: route error response carries no cause category — still "
        f"the static, uninformative 'strategy-builder-error' token with "
        f"nothing else. Response: {body}"
    )
