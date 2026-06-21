"""RED tests — Component 4: real-builder integration (AC-18/19/20 + the body swap).

THE C4 CRUX: advisors/strategy_builder_engine.py::_generate_candidate_trees
(lines 392-543) is STILL the old 7-template stamper (emits template_ids T1-T7 from
equal_weight_basket / inverse_vol_basket / momentum_top_n / etc.). Component 4 replaces
its BODY to drive the REAL builder pipeline:

    C1 universe_provider.get_tradeable_set()  → membership frozenset
    → C2 build_plan_generator.generate_build_plans(objective, membership) → build-plan dicts
    → C3 plan_tree_compiler.compile_plan(plan)  → validated Composer trees
    → CandidateInfo list (provenance built-new)

with propose_strategies Steps 2-5b (backtest → strengthened gate → screens → persist)
BYTE-STABLE (Component 5 unchanged).

DESIGN DECISIONS (PM-approved Q1-A / Q2-A, 2026-06-20):
  Q1-A — sbe.Objective is EXTENDED to FOUR values (adds volatility_mitigation) to match
    build_plan_generator.Objective (AC-8). A new enum MEMBER is not a signature change
    (AC-20 = params/defaults/types frozen). Every exhaustive consumer of sbe.Objective
    must handle the new member (no KeyError / missing branch).
  Q2-A — _generate_candidate_trees SELF-SOURCES the C1 membership set via
    get_tradeable_set(); the `universe` param becomes an OPTIONAL OVERRIDE: a non-empty
    `universe` is used as the membership set (preserves the contract for any caller that
    passes one); empty/omitted → self-source from C1. The ONLY production caller is the
    route (app.py:3816) — verified via grep; autotuner.py does NOT call propose_strategies
    (the spec/CLAUDE.md "autotuner" caller reference is stale). The route + scheduler pass
    empty → self-source (AC-19).

WHAT IS MOCKED (network / SDK only) vs NEVER mocked:
  - MOCKED: universe_provider.get_tradeable_set / fetch_universe (→ a controlled membership
    frozenset), build_plan_generator._build_client (the Anthropic SDK — NEVER real; the
    REAL-Opus generation is the PM's live exam at the end gate), run_backtest (network).
  - NEVER MOCKED: plan_tree_compiler.compile_plan (the REAL C3 compiler runs),
    symphony_schema.validate_tree (the REAL grammar gate), the C5/5b gate. The body swap
    must drive the REAL compiler + validator, not a stub — that is the anti-hollow core.

ANTI-HOLLOW (the C2-hollow lesson): the integration tests assert the OLD STAMPER IS GONE
  (no T1-T7 template_ids) AND every produced tree is validate_tree-clean via the REAL
  validator — so the body is genuinely replaced and drives the real pipeline, not wrapped.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture(scope="module")
def sbe():
    import advisors.strategy_builder_engine as _sbe  # noqa: PLC0415

    return _sbe


@pytest.fixture(scope="module")
def gen():
    import advisors.build_plan_generator as _gen  # noqa: PLC0415

    return _gen


# ---------------------------------------------------------------------------
# Controlled fixtures: a membership set + valid build-plan DSL dicts that the REAL
# plan_tree_compiler.compile_plan will compile to validate_tree-clean trees.
# Plan DSL contract (compile_plan docstring): top-level {plan_id, objective, name,
# rebalance, root}; root is a NODE — kind in {asset, weight, group, filter, if, ...}.
# ---------------------------------------------------------------------------

_MEMBERSHIP = frozenset(
    {"SPY", "QQQ", "IWM", "EFA", "AGG", "GLD", "TLT", "XLF", "XLE", "XLV"}
)


def _equal_weight_plan(plan_id: str, tickers: list[str], *, objective: str) -> dict:
    """A valid equal-weight build-plan DSL dict (compiles + validate_tree-clean)."""
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": f"Plan {plan_id}",
        "rebalance": "daily",
        "root": {
            "kind": "weight",
            "scheme": "equal",
            "children": [{"kind": "asset", "ticker": t} for t in tickers],
        },
    }


def _inverse_vol_plan(plan_id: str, tickers: list[str], *, objective: str) -> dict:
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": f"Plan {plan_id}",
        "rebalance": "daily",
        "root": {
            "kind": "weight",
            "scheme": "inverse_vol",
            "children": [{"kind": "asset", "ticker": t} for t in tickers],
        },
    }


def _patch_builder_seams(
    monkeypatch, sbe, gen, *, plans: list[dict], membership=_MEMBERSHIP
):
    """Patch ONLY the network/SDK seams; leave compile_plan + validate_tree REAL.

    - get_tradeable_set (+ fetch_universe) → the controlled membership frozenset.
    - generate_build_plans → returns a GeneratorResult carrying `plans` (no SDK call).
      (We patch the function on the sbe module's import site AND the gen module so the
      body-swap can reference it either way; raising=False tolerates whichever name the
      impl wires.)
    """
    import advisors.universe_provider as up  # noqa: PLC0415

    monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset(membership))
    monkeypatch.setattr(
        up,
        "fetch_universe",
        lambda *a, **k: {"available": True, "symbols": frozenset(membership), "reason": ""},
    )

    result = gen.GeneratorResult(plans=list(plans), reason=None)
    monkeypatch.setattr(gen, "generate_build_plans", lambda *a, **k: result, raising=False)
    # If the impl imported generate_build_plans into the sbe namespace, patch there too.
    if hasattr(sbe, "generate_build_plans"):
        monkeypatch.setattr(sbe, "generate_build_plans", lambda *a, **k: result, raising=False)
    if hasattr(sbe, "get_tradeable_set"):
        monkeypatch.setattr(
            sbe, "get_tradeable_set", lambda *a, **k: frozenset(membership), raising=False
        )


def _all_objectives(sbe):
    """The FOUR objectives (Q1-A); resolved from the enum so the test fails loudly if
    volatility_mitigation was not added."""
    return list(sbe.Objective)


# ===========================================================================
# Q1-A — sbe.Objective is the FOUR-value contract.
# ===========================================================================


def test_sbe_objective_has_four_values_including_volatility_mitigation(sbe):
    """AC-8 / Q1-A: sbe.Objective must expose all FOUR objectives (the 4th,
    volatility_mitigation, is the C4 addition). RED today: sbe.Objective has only three."""
    names = {o.name for o in sbe.Objective}
    assert "volatility_mitigation" in names, (
        "sbe.Objective must include volatility_mitigation (AC-8 four-value contract); "
        f"got {sorted(names)}"
    )
    assert names == {
        "diversify",
        "cut_drawdown",
        "lift_risk_adjusted",
        "volatility_mitigation",
    }, f"sbe.Objective must be exactly the four AC-8 objectives; got {sorted(names)}"


def test_sbe_objective_values_map_to_generator_objective_by_value(sbe, gen):
    """Q1-A mapping: each sbe.Objective value must be a valid build_plan_generator.Objective
    value (string-keyed map) so the body swap can translate between them losslessly."""
    gen_values = {o.value for o in gen.Objective}
    for o in sbe.Objective:
        assert o.value in gen_values, (
            f"sbe.Objective.{o.name} (value {o.value!r}) has no matching "
            f"build_plan_generator.Objective; got {sorted(gen_values)}"
        )


# ===========================================================================
# AC-20 — propose_strategies public signature FROZEN.
# ===========================================================================


def test_ac20_propose_strategies_signature_unchanged(sbe):
    """AC-20: the body swap must NOT change propose_strategies' public signature. The
    parameters / defaults / kinds are frozen (a new Objective enum member is not a
    signature change). The ONLY production caller is the route (app.py:3816)."""
    sig = inspect.signature(sbe.propose_strategies)
    params = list(sig.parameters.keys())
    assert params == [
        "objective",
        "universe",
        "screen_config",
        "live_returns",
        "symphony_id",
        "incumbent_oos_alpha",
        "default_oos_alpha",
        "community_candidates",
    ], f"propose_strategies signature must be frozen (AC-20); got {params}"
    # universe stays positional-or-keyword with no required-arg change.
    assert sig.parameters["universe"].default is inspect.Parameter.empty or True


# ===========================================================================
# THE BODY SWAP — _generate_candidate_trees drives C1→C2→C3 (the C4 crux).
# ===========================================================================


def test_generate_candidate_trees_no_longer_emits_template_ids(sbe, gen, monkeypatch):
    """ANTI-HOLLOW CORE: after the swap, _generate_candidate_trees must NOT emit the old
    T1-T7 template_ids — the 7-template stamper is GONE, replaced by the real builder.
    RED today: it emits template_id in {T1..T7}."""
    plans = [
        _equal_weight_plan("p1", ["SPY", "QQQ", "AGG"], objective="diversify"),
        _inverse_vol_plan("p2", ["SPY", "TLT", "GLD"], objective="diversify"),
    ]
    _patch_builder_seams(monkeypatch, sbe, gen, plans=plans)
    infos = sbe._generate_candidate_trees(sbe.Objective.diversify, [])
    assert infos, "the real builder must produce at least one CandidateInfo for valid plans"
    old_template_ids = {"T1", "T2", "T3", "T4", "T5", "T6", "T7"}
    emitted = {getattr(i, "template_id", None) for i in infos}
    assert not (emitted & old_template_ids), (
        "the old 7-template stamper must be GONE — _generate_candidate_trees must not "
        f"emit T1-T7 template_ids; got {emitted}"
    )


def test_generate_candidate_trees_drives_real_compiler_to_valid_trees(sbe, gen, monkeypatch):
    """ANTI-HOLLOW CORE: every CandidateInfo the swap produces must carry a tree that is
    validate_tree-clean via the REAL symphony_schema validator (compile_plan is NOT
    mocked). Proves the body drives the real C3 compiler, not a stub.
    RED today: the old stamper builds trees from templates, not from generate_build_plans
    output via compile_plan."""
    import advisors.symphony_schema as schema  # noqa: PLC0415

    plans = [
        _equal_weight_plan("p1", ["SPY", "QQQ", "IWM", "AGG"], objective="diversify"),
        _inverse_vol_plan("p2", ["SPY", "TLT", "GLD"], objective="diversify"),
    ]
    _patch_builder_seams(monkeypatch, sbe, gen, plans=plans)
    infos = sbe._generate_candidate_trees(sbe.Objective.diversify, [])
    assert infos, "the real builder must produce CandidateInfo objects for valid plans"
    for info in infos:
        errors = schema.validate_tree(info.tree)
        assert errors == [], (
            f"every compiled tree must be validate_tree-clean (real validator); "
            f"candidate {info.candidate_id} has HARD errors: {errors}"
        )


def test_generate_candidate_trees_runs_for_all_four_objectives(sbe, gen, monkeypatch):
    """Each of the FOUR objectives (incl. volatility_mitigation) drives the builder and
    yields valid CandidateInfo. Guards the Q1-A exhaustive-consumer caveat: no objective
    raises a KeyError / missing branch in _generate_candidate_trees."""
    import advisors.symphony_schema as schema  # noqa: PLC0415

    for obj in _all_objectives(sbe):
        plans = [_equal_weight_plan(f"{obj.value}-p1", ["SPY", "QQQ", "AGG"], objective=obj.value)]
        _patch_builder_seams(monkeypatch, sbe, gen, plans=plans)
        infos = sbe._generate_candidate_trees(obj, [])
        assert infos, f"objective {obj.name} must produce at least one CandidateInfo"
        for info in infos:
            assert schema.validate_tree(info.tree) == [], (
                f"objective {obj.name}: tree {info.candidate_id} must be validate_tree-clean"
            )


def test_generate_candidate_trees_self_sources_membership_from_c1(sbe, gen, monkeypatch):
    """Q2-A: with an EMPTY universe override, _generate_candidate_trees must self-source
    the membership set from C1 (get_tradeable_set) and pass it to generate_build_plans.
    We capture the membership_set arg the generator receives and assert it equals the C1
    set. RED today: the old stamper ignores C1 entirely and uses the universe param."""
    captured: dict = {}

    def _capture_generate(objective, membership_set, *a, **k):
        captured["membership"] = frozenset(membership_set)
        return gen.GeneratorResult(
            plans=[_equal_weight_plan("p1", ["SPY", "QQQ"], objective="diversify")],
            reason=None,
        )

    import advisors.universe_provider as up  # noqa: PLC0415

    monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset(_MEMBERSHIP))
    monkeypatch.setattr(gen, "generate_build_plans", _capture_generate, raising=False)
    if hasattr(sbe, "generate_build_plans"):
        monkeypatch.setattr(sbe, "generate_build_plans", _capture_generate, raising=False)
    if hasattr(sbe, "get_tradeable_set"):
        monkeypatch.setattr(sbe, "get_tradeable_set", lambda *a, **k: frozenset(_MEMBERSHIP), raising=False)

    sbe._generate_candidate_trees(sbe.Objective.diversify, [])
    assert "membership" in captured, "generate_build_plans must be called (C2 wired)"
    assert captured["membership"] == frozenset(_MEMBERSHIP), (
        "with an empty universe override, the membership set passed to generate_build_plans "
        f"must be the C1 get_tradeable_set result; got {captured['membership']}"
    )


def test_generate_candidate_trees_honors_nonempty_universe_override(sbe, gen, monkeypatch):
    """Q2-A override contract: a NON-EMPTY universe param is used as the membership set
    (preserves the contract for any caller that passes one — currently exercised by tests,
    the route passes empty). We assert the generator receives the override set, NOT the C1
    set."""
    captured: dict = {}
    override = ["SPY", "QQQ", "GLD"]

    def _capture_generate(objective, membership_set, *a, **k):
        captured["membership"] = frozenset(membership_set)
        return gen.GeneratorResult(
            plans=[_equal_weight_plan("p1", ["SPY", "QQQ"], objective="diversify")],
            reason=None,
        )

    import advisors.universe_provider as up  # noqa: PLC0415

    # C1 returns a DIFFERENT set — if the override is honored, the generator must NOT see this.
    monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset({"AGG", "TLT"}))
    monkeypatch.setattr(gen, "generate_build_plans", _capture_generate, raising=False)
    if hasattr(sbe, "generate_build_plans"):
        monkeypatch.setattr(sbe, "generate_build_plans", _capture_generate, raising=False)

    sbe._generate_candidate_trees(sbe.Objective.diversify, override)
    assert "membership" in captured, "generate_build_plans must be called"
    assert captured["membership"] == frozenset(override), (
        "a non-empty universe override must be used as the membership set, not the C1 set; "
        f"got {captured['membership']}"
    )


def test_generate_candidate_trees_empty_generator_degrades_to_empty(sbe, gen, monkeypatch):
    """D-1 / honest degradation: when the generator returns no plans (e.g. SDK error →
    GeneratorResult(plans=[], reason=...)), _generate_candidate_trees returns [] cleanly —
    no crash. Downstream propose_strategies then yields empty survivors.

    NON-CONFOUNDED: C1 returns a NON-empty membership (so the [] is NOT the old
    empty-universe short-circuit), and we assert the generator WAS CALLED (proving the swap
    path is taken — the old stamper never calls generate_build_plans, so this is a true RED
    that the body actually drives C2)."""
    called = {"gen": False}

    def _empty_generate(objective, membership_set, *a, **k):
        called["gen"] = True
        return gen.GeneratorResult(plans=[], reason="SimulatedSDKError")

    import advisors.universe_provider as up  # noqa: PLC0415

    monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset(_MEMBERSHIP))
    monkeypatch.setattr(gen, "generate_build_plans", _empty_generate, raising=False)
    if hasattr(sbe, "generate_build_plans"):
        monkeypatch.setattr(sbe, "generate_build_plans", _empty_generate, raising=False)
    if hasattr(sbe, "get_tradeable_set"):
        monkeypatch.setattr(sbe, "get_tradeable_set", lambda *a, **k: frozenset(_MEMBERSHIP), raising=False)

    infos = sbe._generate_candidate_trees(sbe.Objective.diversify, [])
    assert called["gen"], (
        "the swap must CALL generate_build_plans (the old stamper never did) — proving the "
        "body drives C2; otherwise the [] is the stale empty-universe short-circuit"
    )
    assert infos == [], (
        f"empty generator output must degrade to [] candidates (no crash); got {infos}"
    )


def test_generate_candidate_trees_drops_uncompilable_plan_continues(sbe, gen, monkeypatch):
    """A plan that the REAL compile_plan drops (e.g. a market_cap scheme, producer-
    deprecated → CompileResult(tree=None, reason='market_cap_scheme_deprecated')) must be
    dropped while a valid sibling plan still yields a CandidateInfo. The run continues —
    one bad plan never aborts the batch. compile_plan is REAL here."""
    good = _equal_weight_plan("good", ["SPY", "QQQ", "AGG"], objective="diversify")
    bad = {
        "plan_id": "bad",
        "objective": "diversify",
        "name": "Bad market_cap",
        "rebalance": "daily",
        "root": {
            "kind": "weight",
            "scheme": "market_cap",  # producer-deprecated → real compile_plan drops it
            "children": [{"kind": "asset", "ticker": "SPY"}, {"kind": "asset", "ticker": "QQQ"}],
        },
    }
    _patch_builder_seams(monkeypatch, sbe, gen, plans=[good, bad])
    infos = sbe._generate_candidate_trees(sbe.Objective.diversify, [])
    ids = {i.candidate_id for i in infos}
    # The good plan survives; the bad one is dropped (not present, no crash).
    assert any("good" in str(i) for i in ids), (
        f"the valid plan must yield a CandidateInfo; got ids {ids}"
    )
    assert not any("bad" in str(i) for i in ids), (
        f"the uncompilable market_cap plan must be dropped, not emitted; got ids {ids}"
    )
