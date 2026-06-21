"""RED tests — Component 4 AC-19: on-demand route drives the REAL builder, universe from C1.

The existing POST /ai-advisor/strategy-builder/run route (app.py:3759) is rewired to the
real builder and produces the SAME class of result (survivors/rejected/FDR JSON) as the
weekly run, sourcing the universe from the provider (Component 1) — NOT an operator-supplied
ticker list.

DESIGN (PM-approved Q2-A, 2026-06-20): the C1 self-sourcing happens INSIDE
_generate_candidate_trees (the membership set is fetched via get_tradeable_set); the route's
observable contract change is: it produces a valid builder result WITHOUT requiring an
operator `universe` ticker list (an omitted/empty universe → the builder self-sources from
C1). The response JSON contract {survivors, rejected, n_candidates, fdr_adjusted_threshold,
error} is PRESERVED. Advisory-only / no LIVE_EXECUTION guarantees unchanged.

WHAT IS MOCKED: the builder collaborators at the source module (universe_provider C1, the
generator's _build_client SDK seam, run_backtest) so no live network. The route + the real
_generate_candidate_trees body + compile_plan + gate run for real. The route handler uses
lazy `from X import Y` imports, so patching the SOURCE module namespace is the correct seam.

NO HARDCODED PRODUCER VALUES: assert response SHAPE / keys / the universe-from-C1 behaviour,
never a literal metric.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_live_atlas():
    """Mock load_community_strategies for EVERY route test in this file.

    The route (app.py:3807) calls load_community_strategies(force_refresh=False) before
    propose_strategies — a LIVE Atlas/MongoDB pull. Without this mock the test would hang
    on the network (the loader is wall-clock-bounded but DNS/SRV resolution + retries can
    stall well past pytest's patience). We return an empty-available result so the route's
    community-candidate path is a clean no-op and the test exercises only the builder path.
    Patches the SOURCE module (the route lazy-imports it from advisors.community_strats)."""
    empty = {
        "available": False,
        "reason": "MockedNoAtlasInTests",
        "candidates": [],
        "stats": {"pulled": 0, "valid": 0},
        "source": "captplanet",
    }
    with patch(
        "advisors.community_strats.load_community_strategies", return_value=empty
    ):
        yield


@pytest.fixture
def sb_client():
    import app as app_module  # noqa: PLC0415

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _post_run(client, body: dict | None = None):
    payload = body if body is not None else {"objective": "diversify"}
    return client.post(
        "/ai-advisor/strategy-builder/run",
        data=json.dumps(payload),
        content_type="application/json",
    )


# ===========================================================================
# AC-19 — the route works WITHOUT an operator universe (builder self-sources from C1).
# ===========================================================================


def test_ac19_route_sources_universe_from_c1_not_operator_list(sb_client, monkeypatch):
    """AC-19: with NO `universe` in the request body, the route must still drive the real
    builder by self-sourcing the membership set from C1 (get_tradeable_set). We assert C1
    was consulted and the response carries the preserved contract. RED today: the route
    requires an operator universe and the old stamper ignores C1 — with no universe the run
    yields no candidates without ever consulting C1."""
    import advisors.universe_provider as up  # noqa: PLC0415
    import advisors.build_plan_generator as gen  # noqa: PLC0415

    c1_consulted = {"hit": False}

    def _membership(*a, **k):
        c1_consulted["hit"] = True
        return frozenset({"SPY", "QQQ", "AGG", "GLD"})

    monkeypatch.setattr(up, "get_tradeable_set", _membership)
    monkeypatch.setattr(
        up, "fetch_universe",
        lambda *a, **k: {"available": True, "symbols": frozenset({"SPY", "QQQ", "AGG", "GLD"}), "reason": ""},
    )
    # Generator returns one valid plan (no real SDK call); real compile_plan runs.
    monkeypatch.setattr(
        gen, "generate_build_plans",
        lambda *a, **k: gen.GeneratorResult(
            plans=[{
                "plan_id": "p1", "objective": "diversify", "name": "P1", "rebalance": "daily",
                "root": {"kind": "weight", "scheme": "equal",
                         "children": [{"kind": "asset", "ticker": "SPY"}, {"kind": "asset", "ticker": "QQQ"}]},
            }],
            reason=None,
        ),
        raising=False,
    )
    # run_backtest mocked (no network) — return a date-keyed series so the gate runs.
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415
    _series = {f"2026-{m:02d}-{d:02d}": 0.001 for m in range(1, 5) for d in range(1, 11)}
    monkeypatch.setattr(
        sbe, "run_backtest",
        lambda *a, **k: BacktestResult(stats={"sharpe": 0.4}, data_warnings=[], daily_returns=dict(_series)),
        raising=False,
    )

    resp = _post_run(sb_client, {"objective": "diversify"})  # NO universe supplied
    assert resp.status_code == 200, f"route must return 200; got {resp.status_code}"
    body = resp.get_json()
    # Contract preserved.
    for key in ("survivors", "rejected", "n_candidates", "fdr_adjusted_threshold", "error"):
        assert key in body, f"response must preserve the '{key}' contract key; got {sorted(body)}"
    # The decisive AC-19 assertion: C1 was consulted even though no operator universe was given.
    assert c1_consulted["hit"], (
        "AC-19: with no operator universe, the route/builder must self-source the membership "
        "set from C1 (get_tradeable_set) — C1 was never consulted (the route still relies on "
        "an operator ticker list)."
    )


def test_ac19_route_response_contract_preserved_with_real_builder(sb_client, monkeypatch):
    """AC-19: the response JSON contract is the SAME class as before (survivors/rejected/
    n_candidates/fdr_adjusted_threshold/error), produced via the REAL builder path. Mocks
    only the network/SDK seams; the real _generate_candidate_trees + compile_plan + gate run."""
    import advisors.universe_provider as up  # noqa: PLC0415
    import advisors.build_plan_generator as gen  # noqa: PLC0415
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

    monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset({"SPY", "QQQ", "AGG"}))
    monkeypatch.setattr(
        gen, "generate_build_plans",
        lambda *a, **k: gen.GeneratorResult(
            plans=[{
                "plan_id": "p1", "objective": "diversify", "name": "P1", "rebalance": "daily",
                "root": {"kind": "weight", "scheme": "equal",
                         "children": [{"kind": "asset", "ticker": "SPY"}, {"kind": "asset", "ticker": "AGG"}]},
            }],
            reason=None,
        ),
        raising=False,
    )
    _series = {f"2026-{m:02d}-{d:02d}": 0.001 for m in range(1, 5) for d in range(1, 11)}
    monkeypatch.setattr(
        sbe, "run_backtest",
        lambda *a, **k: BacktestResult(stats={"sharpe": 0.4}, data_warnings=[], daily_returns=dict(_series)),
        raising=False,
    )

    resp = _post_run(sb_client, {"objective": "diversify"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body.get("survivors"), list)
    assert isinstance(body.get("rejected"), list)
    assert "n_candidates" in body
    assert "error" in body
    # No survivor/rejected entry may leak a raw template_id from the old stamper — provenance
    # is built-new from the real builder, not T1-T7.
    for entry in list(body.get("survivors", [])) + list(body.get("rejected", [])):
        tid = entry.get("template_id")
        assert tid not in {"T1", "T2", "T3", "T4", "T5", "T6", "T7"}, (
            f"route response must not carry old-stamper template_ids; got {tid}"
        )


def test_ac19_route_accepts_volatility_mitigation_objective(sb_client, monkeypatch):
    """Q1-A exhaustive-consumer caveat at the ROUTE: the route's objective-string parsing
    must accept the new 'volatility_mitigation' objective (not silently fall back to
    diversify). RED today: Objective('volatility_mitigation') raises ValueError → the route's
    except-branch defaults to diversify, so the new objective is unreachable via the route."""
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    seen = {"objective": None}

    def _capture_propose(objective, *a, **k):
        seen["objective"] = getattr(objective, "value", objective)
        return sbe.ProposalRun(
            candidates=[], gated_batch=sbe._empty_gate_batch(),
            screened_survivors=[], observations_written=0,
        )

    monkeypatch.setattr(sbe, "propose_strategies", _capture_propose, raising=False)

    resp = _post_run(sb_client, {"objective": "volatility_mitigation"})
    assert resp.status_code == 200
    assert seen["objective"] == "volatility_mitigation", (
        "the route must pass the 'volatility_mitigation' objective through to the builder, "
        f"not silently default to diversify; got {seen['objective']!r}"
    )


def test_ac19_route_remains_advisory_only_no_live_execution(sb_client, monkeypatch):
    """AC-22 guard retained through the rewire: the route must NOT touch LIVE_EXECUTION or a
    settings-write path. We assert the response is advisory JSON and (static guard) the route
    source contains no LIVE_EXECUTION mutation."""
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    monkeypatch.setattr(
        sbe, "propose_strategies",
        lambda *a, **k: sbe.ProposalRun(
            candidates=[], gated_batch=sbe._empty_gate_batch(),
            screened_survivors=[], observations_written=0,
        ),
        raising=False,
    )
    resp = _post_run(sb_client, {"objective": "diversify"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "error" in body and "survivors" in body, "advisory JSON contract preserved"
    # Static guard (AC-22): the route must not MUTATE LIVE_EXECUTION or any settings-write
    # allowlist. We parse the route's AST and assert no assignment/subscript-write targets
    # LIVE_EXECUTION or _SETTINGS_WRITE_ALLOWLIST — a docstring/comment that merely MENTIONS
    # "No LIVE_EXECUTION interaction" (which the route legitimately documents) must NOT trip
    # the guard, so a naive substring check is wrong. We look for real writes only.
    import ast  # noqa: PLC0415
    import inspect  # noqa: PLC0415
    import textwrap  # noqa: PLC0415
    import app as app_module  # noqa: PLC0415

    src = textwrap.dedent(inspect.getsource(app_module.ai_advisor_strategy_builder_run))
    tree = ast.parse(src)
    forbidden_write_targets = {"LIVE_EXECUTION", "_SETTINGS_WRITE_ALLOWLIST"}
    write_violations: list[str] = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for tgt in targets:
            for sub in ast.walk(tgt):
                if isinstance(sub, ast.Name) and sub.id in forbidden_write_targets:
                    write_violations.append(sub.id)
                if isinstance(sub, ast.Constant) and sub.value in forbidden_write_targets:
                    write_violations.append(str(sub.value))
    assert not write_violations, (
        "the strategy-builder route must never WRITE LIVE_EXECUTION or mutate the settings "
        f"allowlist (AC-22 advisory-only); found write targets: {write_violations}"
    )
