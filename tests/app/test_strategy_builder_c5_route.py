"""C5 RED — on-demand route parity + provenance end-to-end (AC-19 / AC-13 / AC-23-boundary).

Route under test: POST /ai-advisor/strategy-builder/run  (app.ai_advisor_strategy_builder_run)

THE C5 ROUTE GAP (empirically grounded, 2026-06-20):
  The route TODAY admits community strategies via the OLD, NON-objective-matched
  adapter `strategy_builder_engine.community_candidate_infos` (app.py:3784,3808),
  which tags candidates `template_id="community"` with NO `params["provenance"]`.
  The operator's dual-mode directive (AC-12/AC-13) requires OBJECTIVE-MATCHED
  admission tagged `provenance="atlas-suggested"`, surfaced in the route JSON
  alongside built-new candidates, all gated in ONE pooled FDR batch.

  The objective-matched admission already EXISTS and is tested:
    build_plan_generator.admit_community_candidates(community_result, objective, ...)
    build_plan_generator.load_atlas_candidates(objective, ...)
  but has ZERO production callers — the route never calls it.

  GREEN (quint-flask, app.py only): rewire the route's community admission from
  community_candidate_infos → the objective-matched load_atlas_candidates(objective)
  (or admit_community_candidates(community_result, objective)), so atlas candidates
  reach the route as provenance="atlas-suggested" through the pooled batch, AND the
  route JSON surfaces that provenance per candidate.

ADVERSARIAL FOCUS (a worse implementation must FAIL):
  - AC-13: a route that keeps the old unranked "community" tag (no "atlas-suggested")
    FAILS the provenance assertions.
  - AC-13: a route that gates atlas in a separate batch FAILS the pooled-count check.
  - AC-19: a route that reverts to template (T1-T7) candidates, or hard-requires an
    operator universe list, FAILS the real-builder + universe-from-provider checks.
  - AC-23: a route that echoes a secret-bearing run.error verbatim FAILS the boundary
    leak guard.

MOCK SEAMS (route-layer): patch the lazy-imported source modules —
  - advisors.strategy_builder_engine.propose_strategies (capture call kwargs / shape return)
  - advisors.community_strats.load_community_strategies (fixture dict — overrides the
    tests/app/conftest.py autouse offline stub per-test)
  - the objective-matched admission helper (build_plan_generator.load_atlas_candidates
    / admit_community_candidates) when asserting it is the one the route calls.
NO live Atlas / Composer / Anthropic / Alpaca. NO hardcoded producer values.

CSRF is autouse-disabled by tests/conftest.py; get_api_state_dict + load_state are
autouse-stubbed by tests/app/ + tests/ui/ conftests. Tests here live under tests/app/
to inherit those stubs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_ROUTE = "/ai-advisor/strategy-builder/run"


# ---------------------------------------------------------------------------
# Flask client
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _post(client, body: dict | None = None):
    payload = body if body is not None else {"objective": "diversify", "universe": []}
    return client.post(_ROUTE, data=json.dumps(payload), content_type="application/json")


# ---------------------------------------------------------------------------
# ProposalRun stub builder — shape matches the route's serialisation accesses.
# Provenance is carried on each candidate's CandidateInfo (template_id / params),
# mirroring the real engine so the route's _gate_result_to_dict can surface it.
# ---------------------------------------------------------------------------


def _candidate_info(candidate_id: str, *, provenance: str):
    """A CandidateInfo-shaped object carrying provenance the way the engine does.

    The real engine sets template_id=provenance for built-new and
    params['provenance']=provenance for both sources; we mirror BOTH so the
    route may read provenance from either field.
    """
    return SimpleNamespace(
        candidate_id=candidate_id,
        template_id=provenance,
        params={"provenance": provenance},
        metrics={"sharpe": None},
    )


def _gate_result(candidate_id: str, *, decision: str):
    return SimpleNamespace(
        candidate_id=candidate_id,
        verdict=SimpleNamespace(decision=decision),
        winner_p_adj=None,
        caveats=["hypothesis caveat"],
        rejection_reason=None,
    )


def _proposal_run(*, survivors, rejected, n_candidates, error=None):
    """Build a ProposalRun-shaped object.

    survivors / rejected are lists of (candidate_id, provenance) tuples.
    candidates carries the matching CandidateInfo for each so the route can
    resolve provenance via run.candidates lookup (its real mechanism).
    """
    survivor_results = [_gate_result(cid, decision="ADOPT_CANDIDATE") for cid, _ in survivors]
    rejected_results = [_gate_result(cid, decision="WITHHELD_FDR") for cid, _ in rejected]
    all_infos = [_candidate_info(cid, provenance=prov) for cid, prov in (survivors + rejected)]
    gate = SimpleNamespace(
        results=survivor_results + rejected_results,
        survivors=survivor_results,
        n_candidates=n_candidates,
        fdr_q=0.05,
    )
    return SimpleNamespace(
        candidates=all_infos,
        gated_batch=gate,
        screened_survivors=survivor_results,
        observations_written=len(survivor_results),
        error=error,
    )


def _all_candidate_dicts(data: dict) -> list[dict]:
    return list(data.get("survivors", [])) + list(data.get("rejected", []))


def _provenance_of(cand: dict) -> str | None:
    """Resolve a candidate dict's provenance from whichever field the route exposes."""
    if cand.get("provenance"):
        return cand["provenance"]
    params = cand.get("params") or {}
    if params.get("provenance"):
        return params["provenance"]
    # The route exposes template_id; built-new/atlas tags ride it in the real engine.
    return cand.get("template_id")


# ===========================================================================
# AC-19 — On-demand parity: route exercises the REAL builder.
# ===========================================================================


class TestAC19RouteParity:
    def test_route_calls_propose_strategies_real_builder_path(self, client):
        """AC-19: the route dispatches the REAL builder via propose_strategies (the C4
        body swap drives C1→C2→C3). We assert propose_strategies is the call site."""
        run = _proposal_run(survivors=[], rejected=[], n_candidates=0)
        with patch(
            "advisors.strategy_builder_engine.propose_strategies", return_value=run
        ) as propose_mock:
            resp = _post(client, {"objective": "diversify", "universe": []})
        assert resp.status_code == 200
        assert propose_mock.called, (
            "AC-19: the route must dispatch the real builder via propose_strategies"
        )

    def test_route_preserves_json_contract(self, client):
        """AC-19: the response JSON keeps the survivors/rejected/FDR contract."""
        run = _proposal_run(
            survivors=[("bn1", "built-new")],
            rejected=[("bn2", "built-new")],
            n_candidates=2,
        )
        with patch("advisors.strategy_builder_engine.propose_strategies", return_value=run):
            resp = _post(client, {"objective": "diversify", "universe": []})
        data = resp.get_json()
        required = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold", "error"}
        assert required <= set(data.keys()), (
            f"AC-19: route JSON must preserve {sorted(required)}; got {sorted(data.keys())}"
        )
        assert isinstance(data["survivors"], list) and isinstance(data["rejected"], list)

    def test_route_sources_universe_from_provider_when_empty(self, client):
        """AC-19: with an EMPTY universe in the request body, the route must NOT hard-fail
        or require an operator ticker list — it passes an empty universe so the engine
        self-sources from Component 1. We assert propose_strategies receives universe=[]
        (data-driven), not a fabricated default list."""
        run = _proposal_run(survivors=[], rejected=[], n_candidates=0)
        with patch(
            "advisors.strategy_builder_engine.propose_strategies", return_value=run
        ) as propose_mock:
            resp = _post(client, {"objective": "diversify"})  # no universe key at all
        assert resp.status_code == 200
        assert propose_mock.called
        _, kwargs = propose_mock.call_args
        passed_universe = kwargs.get("universe")
        if passed_universe is None and propose_mock.call_args.args:
            # tolerate positional universe if the route ever switches
            passed_universe = propose_mock.call_args.args[1]
        assert passed_universe == [], (
            "AC-19: an absent/empty request universe must flow to the engine as an empty "
            f"list so C1 self-sources the membership set; got universe={passed_universe!r}"
        )


# ===========================================================================
# AC-13 — Provenance end-to-end: built-new AND atlas-suggested in route JSON.
# ===========================================================================


class TestAC13ProvenanceEndToEnd:
    def test_route_surfaces_built_new_provenance(self, client):
        """AC-13: a built-new survivor surfaces provenance='built-new' in the route JSON."""
        run = _proposal_run(
            survivors=[("bn1", "built-new")],
            rejected=[],
            n_candidates=1,
        )
        with patch("advisors.strategy_builder_engine.propose_strategies", return_value=run):
            resp = _post(client, {"objective": "diversify", "universe": []})
        data = resp.get_json()
        cands = _all_candidate_dicts(data)
        assert cands, "expected at least one candidate in the route JSON"
        provs = {_provenance_of(c) for c in cands}
        assert "built-new" in provs, (
            f"AC-13: route JSON must surface provenance='built-new' for built-new "
            f"candidates; got provenances {provs}"
        )

    def test_route_surfaces_atlas_suggested_provenance(self, client):
        """AC-13 (the RED gap): an atlas-suggested candidate must surface
        provenance='atlas-suggested' in the route JSON.

        TODAY the route admits community candidates via the unranked
        community_candidate_infos adapter, which tags them template_id='community'
        with NO 'atlas-suggested' provenance — so this FAILS until the route is
        rewired to the objective-matched admission. We drive the route end-to-end:
        load_community_strategies returns available atlas docs; propose_strategies is
        the REAL function (so the route's own admission wiring decides the tag)."""
        atlas_result = {
            "available": True,
            "candidates": [
                {
                    "sid": "atlas-1",
                    "name": "Community A",
                    "tree": {
                        "step": "wt-cash-equal",
                        "children": [{"step": "asset", "ticker": "SPY"}],
                    },
                    "oos_metrics": {"sharpe": 1.2, "max_drawdown": -0.1, "volatility": 0.1},
                    "composition_hash": "h1",
                }
            ],
            "stats": {"pulled": 1, "valid": 1},
            "source": "captplanet",
        }

        # Capture whatever community_candidates the route forwards to the engine, then
        # return a ProposalRun whose JSON reflects THOSE candidates' provenance tags.
        captured: dict = {}

        def _fake_propose(*args, **kwargs):
            cc = kwargs.get("community_candidates") or []
            captured["community_candidates"] = cc
            survivors = []
            for ci in cc:
                params = getattr(ci, "params", {}) or {}
                prov = params.get("provenance") or getattr(ci, "template_id", None)
                survivors.append((getattr(ci, "candidate_id", "atlas-?"), prov))
            return _proposal_run(survivors=survivors, rejected=[], n_candidates=len(survivors))

        with (
            patch(
                "advisors.community_strats.load_community_strategies",
                return_value=atlas_result,
            ),
            patch(
                "advisors.strategy_builder_engine.propose_strategies",
                side_effect=_fake_propose,
            ),
        ):
            resp = _post(client, {"objective": "cut_drawdown", "universe": []})

        assert resp.status_code == 200
        assert captured.get("community_candidates"), (
            "AC-13: the route must admit the available atlas community candidates and "
            "forward them to propose_strategies; it forwarded none."
        )
        data = resp.get_json()
        provs = {_provenance_of(c) for c in _all_candidate_dicts(data)}
        assert "atlas-suggested" in provs, (
            "AC-13 (RED gap): atlas community candidates must reach the route JSON tagged "
            f"provenance='atlas-suggested' (NOT the old 'community' tag); got {provs}. "
            "GREEN: rewire the route's community admission to the objective-matched "
            "build_plan_generator.load_atlas_candidates(objective)."
        )

    def test_route_pools_both_provenance_sources_in_one_batch(self, client):
        """AC-13/AC-21: built-new AND atlas-suggested candidates are surfaced together and
        the FDR batch count includes BOTH. We return a ProposalRun mixing both provenances
        and assert n_candidates counts both + both tags appear in the JSON."""
        run = _proposal_run(
            survivors=[("bn1", "built-new"), ("atlas-1", "atlas-suggested")],
            rejected=[("bn2", "built-new"), ("atlas-2", "atlas-suggested")],
            n_candidates=4,
        )
        with patch("advisors.strategy_builder_engine.propose_strategies", return_value=run):
            resp = _post(client, {"objective": "diversify", "universe": []})
        data = resp.get_json()
        assert data["n_candidates"] == 4, (
            "AC-21: the FDR batch count must include BOTH provenance sources "
            f"(2 built-new + 2 atlas = 4); got {data['n_candidates']}"
        )
        provs = {_provenance_of(c) for c in _all_candidate_dicts(data)}
        assert {"built-new", "atlas-suggested"} <= provs, (
            f"AC-13: both provenance values must appear in the route JSON; got {provs}"
        )

    def test_route_uses_objective_matched_admission_not_old_adapter(self, client):
        """AC-13 (contract upgrade): the route must use the OBJECTIVE-MATCHED admission
        (build_plan_generator.load_atlas_candidates / admit_community_candidates), NOT the
        unranked strategy_builder_engine.community_candidate_infos.

        Assert the objective-matched helper IS called with the request objective. This is
        the AC-12/AC-13 dual-mode upgrade: the old adapter ignored the objective entirely.
        RED today: the route calls community_candidate_infos and never the matched helper."""
        atlas_result = {
            "available": True,
            "candidates": [
                {
                    "sid": "atlas-1",
                    "name": "Community A",
                    "tree": {
                        "step": "wt-cash-equal",
                        "children": [{"step": "asset", "ticker": "SPY"}],
                    },
                    "oos_metrics": {"max_drawdown": -0.05, "volatility": 0.08, "sharpe": 1.0},
                    "composition_hash": "h1",
                }
            ],
            "stats": {"pulled": 1, "valid": 1},
            "source": "captplanet",
        }
        run = _proposal_run(survivors=[], rejected=[], n_candidates=0)

        import advisors.build_plan_generator as bpg  # noqa: PLC0415

        matched_admission = MagicMock(wraps=bpg.load_atlas_candidates)

        with (
            patch(
                "advisors.community_strats.load_community_strategies",
                return_value=atlas_result,
            ),
            patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
            patch.object(bpg, "load_atlas_candidates", matched_admission),
        ):
            resp = _post(client, {"objective": "cut_drawdown", "universe": []})

        assert resp.status_code == 200
        assert matched_admission.called, (
            "AC-13/AC-12 (RED gap): the route must call the OBJECTIVE-MATCHED admission "
            "(build_plan_generator.load_atlas_candidates), not the unranked "
            "community_candidate_infos. It was never called."
        )
        # The matched helper must receive the request objective so admission is objective-shaped.
        call = matched_admission.call_args
        objective_arg = None
        if call.args:
            objective_arg = call.args[0]
        objective_arg = objective_arg if objective_arg is not None else call.kwargs.get("objective")
        objective_value = getattr(objective_arg, "value", objective_arg)
        assert objective_value == "cut_drawdown", (
            "AC-12: objective-matched admission must receive the request objective "
            f"('cut_drawdown'); got {objective_value!r}"
        )

    def test_old_unranked_community_adapter_is_gone(self, client):
        """AC-13 / EDGE-1 (contract upgrade): the old unranked community_candidate_infos
        adapter must be DELETED from strategy_builder_engine — so the route structurally
        CANNOT call it. (Originally this patched the adapter and asserted not-called; once
        the adapter was deleted, patching a non-existent attribute errors, so this is
        re-pointed to assert the deletion — the durable post-rewire contract. The route's
        positive use of the objective-matched admission is asserted in
        test_route_uses_objective_matched_admission_not_old_adapter above.)"""
        import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

        assert not hasattr(sbe, "community_candidate_infos"), (
            "EDGE-1: the old unranked community_candidate_infos adapter must be deleted; "
            "the route uses the objective-matched build_plan_generator.load_atlas_candidates."
        )


# ===========================================================================
# AC-23 (boundary) — route JSON never leaks a secret, degrades honestly.
# ===========================================================================


class TestAC23RouteBoundary:
    def test_route_does_not_leak_secret_from_engine_error(self, client):
        """AC-23/security: when propose_strategies returns a secret-bearing run.error, the
        route JSON 'error' must NOT echo it verbatim — the operator-observable boundary
        must surface only a safe class-name-style token. RED if the route passes
        str(run.error) straight through."""
        run = _proposal_run(
            survivors=[],
            rejected=[],
            n_candidates=0,
            error="RuntimeError: sk-ant-LEAKED-SECRET APCA-API-KEY-ID Bearer abc123",
        )
        with patch("advisors.strategy_builder_engine.propose_strategies", return_value=run):
            resp = _post(client, {"objective": "diversify", "universe": []})
        assert resp.status_code == 200
        data = resp.get_json()
        err = data.get("error") or ""
        for secret in ("sk-ant-", "APCA-API-KEY-ID", "Bearer abc123"):
            assert secret not in err, (
                "AC-23/security: the route JSON error must not leak a secret-bearing engine "
                f"error verbatim; found {secret!r} in error={err!r}. GREEN: the route must "
                "sanitise run.error (surface a safe token, not raw str)."
            )
        assert data.get("survivors") == [] and data.get("rejected") == [], (
            "AC-23: on an engine error the route returns empty survivors/rejected"
        )

    def test_route_does_not_500_when_atlas_load_raises(self, client):
        """AC-23: an Atlas load failure must not 500 the route — it degrades to a
        template-only (built-new) run. Already guarded today; locks it for the rewire."""
        run = _proposal_run(survivors=[("bn1", "built-new")], rejected=[], n_candidates=1)
        with (
            patch(
                "advisors.community_strats.load_community_strategies",
                side_effect=RuntimeError("atlas down"),
            ),
            patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        ):
            resp = _post(client, {"objective": "diversify", "universe": []})
        assert resp.status_code == 200, (
            "AC-23: an Atlas load failure must degrade (200, template-only run), never 500"
        )
        data = resp.get_json()
        assert "survivors" in data and "rejected" in data
