"""RED tests — R2-1 AC-4/AC-5(route half)/AC-7: SB run-route provenance JSON.

Contract pinned (per r2-fe/r2-engine, confirmed via SendMessage before this
file was written):

  POST /ai-advisor/strategy-builder/run response JSON gains a "provenance" key:
    {"generation_model": <str>, "mode": "build-new",
     "evidence_injected": <the AC-3 manifest dict>, "run_id": <uuid4 str>}

  This 4-key JSON object is assembled by the ROUTE, merging
  ProposalRun.provenance (a 3-key dict: generation_model/mode/evidence_injected
  — engine-side, tested in test_strategy_builder_engine_reasoning_provenance.py)
  with ProposalRun.run_id (a separate top-level field) — the route is the ONLY
  place all 4 keys come together into one JSON object.

  The route must read run.provenance / run.run_id via getattr(run, ..., None)
  — NOT direct attribute access — because several pre-existing test fixtures
  in this suite build bare MagicMock() ProposalRun stand-ins (auto-vivifying
  Mocks would otherwise break jsonify()). This file tests that contract
  directly (test_route_survives_bare_mock_run_missing_provenance_attrs);
  separately, tests/ui/test_strategy_builder_community_wiring.py and this
  file's own pre-existing Q-29-style test are updated to set explicit
  provenance/run_id defaults (see the same commit).

Credential-less (AC-7): every test here mocks propose_strategies (and, for
the wiring test, build_reasoning_context) directly — zero live Composer/
Anthropic calls regardless of .env contents.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app as app_module


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _make_run(*, provenance=None, run_id=None, error=None, survivors=False):
    """A ProposalRun-shaped SimpleNamespace listing every attribute the route
    reads — SimpleNamespace (not a bare MagicMock) so a missing attribute
    fails loudly with AttributeError rather than silently auto-vivifying,
    matching the precedent set by test_strategy_builder_route.py's
    _make_proposal_run()."""
    gate_batch = SimpleNamespace(results=[], survivors=[], n_candidates=1, fdr_q=0.05)
    survivor_result = SimpleNamespace(
        candidate_id="cand-1",
        verdict=SimpleNamespace(decision="ADOPT_CANDIDATE"),
        caveats=[],
        winner_p_adj=None,
        metrics={},
        template_id="built-new",
    )
    return SimpleNamespace(
        candidates=[],
        gated_batch=gate_batch,
        screened_survivors=[survivor_result] if survivors else [],
        observations_written=1 if survivors else 0,
        error=error,
        error_category=None,
        backtest_unavailable=False,
        backtest_unavailable_count=0,
        provenance=provenance,
        run_id=run_id,
    )


def _post_run(client, body: dict | None = None):
    payload = body or {
        "objective": "diversify",
        "universe": ["SPY", "QQQ"],
        "symphony_id": "sym-hash-1",
    }
    return client.post(
        "/ai-advisor/strategy-builder/run",
        json=payload,
        content_type="application/json",
    )


# ===========================================================================
# AC-4: response JSON carries a 4-key provenance object.
# ===========================================================================


def test_response_json_carries_provenance_key_merging_run_provenance_and_run_id(client):
    """AC-4: the route JSON's 'provenance' object must merge
    ProposalRun.provenance (3 keys) with ProposalRun.run_id into the 4-key
    contract: generation_model / mode / evidence_injected / run_id."""
    manifest = {"tree": "present", "stats": "absent"}
    run = _make_run(
        provenance={
            "generation_model": "test-marker-sb-model",
            "mode": "build-new",
            "evidence_injected": manifest,
        },
        run_id="22222222-2222-4222-8222-222222222222",
        survivors=True,
    )

    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = _post_run(client)

    assert resp.status_code == 200, f"Route returned {resp.status_code}, expected 200."
    data = resp.get_json()
    assert data is not None
    provenance = data.get("provenance")
    assert provenance is not None, (
        f"AC-4 GAP: response JSON has no 'provenance' key (or it is null) on a successful "
        f"run with run.provenance set. Response keys: {sorted(data.keys())!r}"
    )
    assert provenance.get("generation_model") == "test-marker-sb-model", (
        f"provenance={provenance!r}"
    )
    assert provenance.get("mode") == "build-new", f"provenance={provenance!r}"
    assert provenance.get("evidence_injected") == manifest, f"provenance={provenance!r}"
    assert provenance.get("run_id") == "22222222-2222-4222-8222-222222222222", (
        f"AC-4 GAP: response JSON provenance['run_id'] must equal run.run_id — the route "
        f"must merge the two ProposalRun fields into one JSON object. provenance={provenance!r}"
    )


def test_provenance_absent_when_run_provenance_is_none(client):
    """AC-7 (route half): when run.provenance is None (e.g. the engine's
    no-key error early-return), the response JSON must carry provenance=None
    — never a fabricated object with empty/placeholder values."""
    run = _make_run(
        provenance=None,
        run_id="33333333-3333-4333-8333-333333333333",
        error="No Composer API key configured.",
    )

    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = _post_run(client)

    data = resp.get_json()
    assert data is not None
    assert data.get("provenance") is None, (
        f"AC-7 GAP: provenance must be None/absent when run.provenance is None (no-key "
        f"error path). Got {data.get('provenance')!r} — never fabricate provenance."
    )


# ===========================================================================
# Wiring: the route actually calls build_reasoning_context + threads its
# output into propose_strategies(reasoning_context=..., reasoning_manifest=...).
# ===========================================================================


def test_route_threads_build_reasoning_context_output_into_propose_strategies(client):
    """AC-1/AC-2 (route wiring): for a symphony-scoped run, the route must
    call ai_advisor.build_reasoning_context and forward its EXACT return
    values into propose_strategies(reasoning_context=..., reasoning_manifest=...)
    — proven via call_args, not by re-testing assembler internals."""
    known_prompt_context = "## OPERATOR CONTEXT\nHOLD SPY\n"
    known_manifest = {"tree": "present", "stats": "present"}
    run = _make_run(
        provenance={
            "generation_model": "m",
            "mode": "build-new",
            "evidence_injected": known_manifest,
        },
        run_id="44444444-4444-4444-8444-444444444444",
    )
    propose_mock = MagicMock(return_value=run)

    with (
        patch.object(
            app_module.ai_advisor,
            "build_reasoning_context",
            return_value=(known_prompt_context, known_manifest),
        ) as brc_mock,
        patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = _post_run(
            client, {"objective": "diversify", "universe": ["SPY"], "symphony_id": "sym-hash-1"}
        )

    assert resp.status_code == 200
    assert brc_mock.called, (
        "AC-1/AC-2 GAP: ai_advisor.build_reasoning_context was never called for a "
        "symphony-scoped run — the route does not build reasoning context at all."
    )
    assert propose_mock.called, "propose_strategies was never called."
    _call_args, call_kwargs = propose_mock.call_args
    assert call_kwargs.get("reasoning_context") == known_prompt_context, (
        f"AC-1 GAP: propose_strategies was not called with reasoning_context== the "
        f"build_reasoning_context return value. call_kwargs={call_kwargs!r}"
    )
    assert call_kwargs.get("reasoning_manifest") == known_manifest, (
        f"AC-2 GAP: propose_strategies was not called with reasoning_manifest== the "
        f"build_reasoning_context manifest. call_kwargs={call_kwargs!r}"
    )


def test_route_does_not_build_reasoning_context_when_symphony_id_absent(client):
    """AC-8 (from-scratch byte-preservation, route half): when no symphony_id
    is supplied, the route must NOT call build_reasoning_context at all —
    zero extra I/O on the untouched from-scratch path."""
    run = _make_run(provenance=None, run_id="55555555-5555-4555-8555-555555555555")

    with (
        patch.object(app_module.ai_advisor, "build_reasoning_context") as brc_mock,
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = _post_run(client, {"objective": "diversify", "universe": ["SPY"], "symphony_id": ""})

    assert resp.status_code == 200
    brc_mock.assert_not_called()


# ===========================================================================
# Robustness: getattr-with-default, not direct attribute access (front-runs
# the exact bare-MagicMock() break r2-fe flagged — tested as a first-class
# contract here, not only patched into the two affected legacy fixtures).
# ===========================================================================


def test_route_survives_bare_mock_run_missing_provenance_attrs(client):
    """ADVERSARIAL: a bare MagicMock() ProposalRun stand-in with NO
    provenance/run_id explicitly set (auto-vivifies non-None child Mocks on
    attribute access) must not crash jsonify() — proves the route reads
    getattr(run, 'provenance', None) / getattr(run, 'run_id', None), not
    direct attribute access. Same bug class as the pre-existing
    backtest_unavailable incident this cycle is explicitly front-running."""
    run = MagicMock()
    run.error = None
    gate = MagicMock()
    gate.n_candidates = 0
    gate.fdr_q = 0.05
    gate.results = []
    gate.survivors = []
    run.gated_batch = gate
    run.candidates = []
    run.screened_survivors = []
    run.backtest_unavailable = False
    run.backtest_unavailable_count = 0
    # Deliberately NOT setting run.provenance / run.run_id — a bare MagicMock
    # auto-vivifies both as non-None child Mocks on access.

    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = _post_run(client)

    assert resp.status_code == 200, (
        f"Route returned {resp.status_code} instead of 200 against a bare-MagicMock "
        "ProposalRun missing provenance/run_id — jsonify() likely choked on an "
        "auto-vivified child Mock, proving the route used direct attribute access "
        "instead of getattr(run, 'provenance', None)."
    )
    data = resp.get_json()
    assert data is not None, "Response body must still be valid JSON."


# ===========================================================================
# Regression: existing security/scope invariants untouched by this route change.
# ===========================================================================


def test_live_execution_key_never_in_response_with_provenance_present(client):
    """Regression (mirrors the existing C-11 contract): adding provenance
    must not create a new path for LIVE_EXECUTION to leak into the response."""
    run = _make_run(
        provenance={"generation_model": "m", "mode": "build-new", "evidence_injected": {}},
        run_id="66666666-6666-4666-8666-666666666666",
        survivors=True,
    )
    with (
        patch("advisors.strategy_builder_engine.propose_strategies", return_value=run),
        patch("advisors.build_plan_generator.load_atlas_candidates", return_value=[]),
    ):
        resp = _post_run(client)

    import json as _json

    raw = _json.dumps(resp.get_json())
    assert "LIVE_EXECUTION" not in raw, (
        "Regression: 'LIVE_EXECUTION' must never appear in the response, even with "
        "the new provenance object present."
    )


def test_settings_write_allowlist_unaffected_by_provenance_feature():
    """Regression (mirrors the existing G-18 contract): the provenance feature
    must not expand _SETTINGS_WRITE_ALLOWLIST."""
    allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
    assert "strategy_builder" not in allowlist
    assert "LIVE_EXECUTION" not in allowlist
    assert "provenance" not in allowlist
