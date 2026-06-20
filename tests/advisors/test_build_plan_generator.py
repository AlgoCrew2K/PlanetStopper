"""RED tests — Component 2: Opus Build-Plan Generator (advisors/build_plan_generator.py).

Module under test: advisors.build_plan_generator (NEW — does not exist yet; the
ImportError / AttributeError is the first RED signal).

SCOPE: Component 2 ONLY (AC-7..AC-11). Component 1 (universe_provider) is DONE and
USED here (membership validation, AC-9) — never modified. Component 3 (compiler +
wt-marketcap), 4 (scheduler), 5/5b (route + cull) are later phases.

CONTRACT SOURCES (no guessing):
  - The PM-approved build-plan DSL (see .claude/tdd-handoff.md) — the canonical
    generator↔compiler contract. A build-plan is a JSON-serializable dict; NODE is
    a tagged union on `kind`; each kind/scheme is a 1:1 pre-image of a
    symphony_schema constructor.
  - advisors/symphony_schema.py vocabulary frozensets (KNOWN_COMPARATORS,
    KNOWN_REBALANCE, _KNOWN_OPERATORS) — imported live so the enum-membership
    assertions never hardcode the vocabulary.
  - The Anthropic SDK factory seam: tests patch
    advisors.build_plan_generator._build_client (mirrors ai_advisor._build_client,
    ai_advisor.py:1590). NO live network — the SDK is always mocked.

ADVERSARIAL FOCUS (assert SHAPE / STRUCTURE / PRESENCE / ENUM-MEMBERSHIP only —
NEVER hardcoded producer-computed values; the math engine is never mocked):
  - AC-7: a mocked tool-use response carrying N build-plan dicts parses into N
    well-formed plan objects; each conforms to the DSL shape.
  - AC-8: each of the FOUR objectives hard-shapes structure with a DISTINCT,
    mutually-distinguishable structural signature (refinement B: lift_risk_adjusted
    requires a genuine momentum/quality FILTER, not a bare specified-weight tilt).
  - AC-9: off-universe tickers are pruned/the plan rejected before reaching the
    admitted output; degenerate prune => reject, never emit a broken plan
    (refinement A). The "off-universe never admitted" invariant is the property
    test in test_build_plan_generator_property.py.
  - AC-10: N_PLANS_PER_OBJECTIVE == 12 named constant; structurally-identical plans
    DEDUPED (refinement C); a 12-clone batch FAILS the diversity assertion.
  - AC-11: D-1 honest degradation — missing key / SDK error / unparseable response
    => empty plan list + reason; never raises; reason leaks no key/path
    (type(exc).__name__ only).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from advisors import symphony_schema

# ---------------------------------------------------------------------------
# Module-under-test import guard. The module does not exist yet, so this import
# is the first RED failure. Tests reference `bpg` via the fixture below.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bpg():
    """Import and return the build_plan_generator module (RED until it exists)."""
    import advisors.build_plan_generator as _bpg  # noqa: PLC0415

    return _bpg


# ---------------------------------------------------------------------------
# Objective resolution helper — the four-value enum is the AC-8 input contract.
# Resolved from the module under test so the test never hardcodes the enum type.
# ---------------------------------------------------------------------------

_OBJECTIVE_NAMES = (
    "diversify",
    "cut_drawdown",
    "lift_risk_adjusted",
    "volatility_mitigation",
)


def _objective(bpg, name: str):
    """Resolve an Objective enum member by canonical name from the module.

    The module re-exports / defines a four-value Objective enum. We look it up by
    name so the test asserts the four values EXIST (AC-8) without hardcoding the
    enum's identity.
    """
    obj_enum = bpg.Objective
    return obj_enum[name]


# ---------------------------------------------------------------------------
# DSL fixture builders — these construct VALID build-plan NODE dicts by SHAPE.
# They never assert producer values; they are inputs the generator's mocked SDK
# "returns", so we control them entirely (no hardcoded producer output).
# ---------------------------------------------------------------------------


def _asset(ticker: str) -> dict:
    return {"kind": "asset", "ticker": ticker}


def _weight(scheme: str, children: list, **extra) -> dict:
    node = {"kind": "weight", "scheme": scheme, "children": children}
    node.update(extra)
    return node


def _specified(pairs: list[tuple[dict, float]]) -> dict:
    return {
        "kind": "weight",
        "scheme": "specified",
        "children": [{"node": n, "pct": p} for n, p in pairs],
    }


def _group(name: str, children: list) -> dict:
    return {"kind": "group", "name": name, "children": children}


def _filter(select_fn: str, select_n: int, sort_by_fn: str, window: int, children: list) -> dict:
    return {
        "kind": "filter",
        "select_fn": select_fn,
        "select_n": select_n,
        "sort_by_fn": sort_by_fn,
        "window": window,
        "children": children,
    }


def _if_fixed(lhs_fn: str, lhs_ticker: str, window: int, comparator: str, fixed, then, els) -> dict:
    return {
        "kind": "if",
        "condition": {
            "lhs_fn": lhs_fn,
            "lhs_ticker": lhs_ticker,
            "window": window,
            "comparator": comparator,
            "rhs": {"fixed": fixed},
        },
        "then": then,
        "else": els,
    }


def _if_ticker_cmp(lhs_fn, lhs_ticker, rhs_fn, rhs_ticker, window, comparator, then, els) -> dict:
    """An `if` node whose condition compares an indicator on lhs_ticker against the
    same/another indicator on rhs_ticker (the non-fixed ticker-comparison form)."""
    return {
        "kind": "if",
        "condition": {
            "lhs_fn": lhs_fn,
            "lhs_ticker": lhs_ticker,
            "window": window,
            "comparator": comparator,
            "rhs": {"ticker": rhs_ticker, "fn": rhs_fn, "window": window},
        },
        "then": then,
        "else": els,
    }


def _plan(objective: str, root: dict, *, plan_id: str = "p", name: str = "Plan") -> dict:
    """Assemble a top-level build-plan dict by SHAPE (no producer values)."""
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": name,
        "rebalance": "daily",
        "root": root,
    }


# ---------------------------------------------------------------------------
# Mocked-SDK tool-use plumbing. The generator calls _build_client() then the
# client's messages.create(...) with a tool, and reads the tool_use block's
# `.input` (a dict carrying the build-plans). We mirror the anthropic SDK shape.
# ---------------------------------------------------------------------------


def _tool_use_block(input_payload: dict) -> MagicMock:
    """Build a MagicMock mimicking an anthropic tool_use content block.

    A real tool_use block has .type == "tool_use" and .input == <dict>. The
    generator extracts the build-plans from .input; we never assert on producer
    content, only feed the payload we control.
    """
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_payload
    block.name = "emit_build_plans"
    return block


def _client_returning_plans(plans: list[dict]) -> MagicMock:
    """A mock client whose messages.create returns a tool_use block of plans."""
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [_tool_use_block({"plans": plans})]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _patch_client(bpg, client: MagicMock):
    """Patch the module's _build_client factory to return the given client."""
    return patch.object(bpg, "_build_client", return_value=client)


# ---------------------------------------------------------------------------
# DSL shape validator (test-local — NOT production code). Walks a build-plan and
# asserts every NODE conforms to the approved DSL: valid `kind`, valid `scheme`,
# enum-membership on comparator/operator/rebalance. Vocabulary is imported live
# from symphony_schema so nothing is hardcoded.
# ---------------------------------------------------------------------------

_VALID_KINDS = {"asset", "weight", "group", "filter", "if", "if_compound"}
_VALID_SCHEMES = {"equal", "specified", "inverse_vol", "market_cap"}
_VALID_CONDITION_TYPES = {"binary", "binary_compound", "compound"}


def _assert_condition_shape(cond: dict):
    assert isinstance(cond, dict)
    ctype = cond.get("type")
    assert ctype in _VALID_CONDITION_TYPES, f"bad condition type {ctype!r}"
    if ctype == "binary":
        assert cond["comparator"] in symphony_schema.KNOWN_COMPARATORS
    elif ctype == "binary_compound":
        assert cond["comparator"] in symphony_schema.KNOWN_COMPARATORS
        assert cond["operator"] in symphony_schema._KNOWN_OPERATORS
        assert isinstance(cond["tickers"], list) and cond["tickers"]
    elif ctype == "compound":
        assert cond["operator"] in symphony_schema._KNOWN_OPERATORS
        assert isinstance(cond["conditions"], list) and cond["conditions"]
        for sub in cond["conditions"]:
            _assert_condition_shape(sub)


def _assert_node_shape(node: dict):
    """Recursively assert a NODE conforms to the approved DSL by shape/enum."""
    assert isinstance(node, dict), f"node must be a dict, got {type(node).__name__}"
    kind = node.get("kind")
    assert kind in _VALID_KINDS, f"unknown kind {kind!r}"

    if kind == "asset":
        assert isinstance(node.get("ticker"), str) and node["ticker"]
    elif kind == "weight":
        assert node.get("scheme") in _VALID_SCHEMES, f"bad scheme {node.get('scheme')!r}"
        children = node.get("children")
        assert isinstance(children, list) and children
        if node["scheme"] == "specified":
            for entry in children:
                assert "node" in entry and "pct" in entry
                _assert_node_shape(entry["node"])
        else:
            for child in children:
                _assert_node_shape(child)
    elif kind == "group":
        assert isinstance(node.get("name"), str)
        children = node.get("children")
        assert isinstance(children, list) and children
        for child in children:
            _assert_node_shape(child)
    elif kind == "filter":
        assert node.get("select_fn") in {"top", "bottom"}
        assert isinstance(node.get("select_n"), int)
        assert isinstance(node.get("sort_by_fn"), str) and node["sort_by_fn"]
        for child in node.get("children", []):
            _assert_node_shape(child)
    elif kind == "if":
        cond = node.get("condition")
        assert isinstance(cond, dict)
        assert cond["comparator"] in symphony_schema.KNOWN_COMPARATORS
        for child in node.get("then", []) + node.get("else", []):
            _assert_node_shape(child)
    elif kind == "if_compound":
        _assert_condition_shape(node.get("condition"))
        for child in node.get("then", []) + node.get("else", []):
            _assert_node_shape(child)


def _assert_plan_shape(plan: dict):
    """Assert a top-level build-plan conforms to the approved DSL."""
    assert isinstance(plan, dict)
    assert isinstance(plan.get("plan_id"), str) and plan["plan_id"]
    assert plan.get("objective") in _OBJECTIVE_NAMES
    assert isinstance(plan.get("name"), str)
    assert plan.get("rebalance") in symphony_schema.KNOWN_REBALANCE
    _assert_node_shape(plan.get("root"))


# ---------------------------------------------------------------------------
# Structural-signature predicates (test-local) — walk the DSL and report which
# structural features a plan exhibits. Used by AC-8 to assert per-objective
# signatures. These walk the DSL, NOT the prompt, NOT producer text.
# ---------------------------------------------------------------------------

# Momentum/quality sort-by indicators that satisfy the lift_risk_adjusted FILTER
# signature (refinement B). cumulative-return = momentum; moving-average-return =
# trend/quality. Both are real indicator fns in KNOWN_INDICATOR_FNS.
_MOMENTUM_QUALITY_SORTS = {"cumulative-return", "moving-average-return"}
# Low/min-vol selection sort indicators that satisfy volatility_mitigation.
_LOW_VOL_SORTS = {"max-drawdown", "standard-deviation-return", "standard-deviation-price"}


def _iter_nodes(node):
    """Iterative DFS over all NODE dicts in a plan root (test helper)."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, dict):
            continue
        yield cur
        if cur.get("kind") == "weight" and cur.get("scheme") == "specified":
            for entry in cur.get("children", []):
                if isinstance(entry, dict) and "node" in entry:
                    stack.append(entry["node"])
        else:
            for child in cur.get("children", []):
                stack.append(child)
        for branch in ("then", "else"):
            for child in cur.get(branch, []) or []:
                stack.append(child)


# --- SINGLE SOURCE OF TRUTH (PM guardrail, AC-8 decision B) -----------------
# The per-objective structural signature predicate is the MODULE's public
# `plan_matches_objective(plan, objective) -> bool`. The generator filters with it
# (admission floor) and these tests assert with it — they cannot drift. The
# test-local _objective_signature factory was REMOVED; this thin wrapper delegates
# to the module so the assertions and the filter are literally the same code.
#
# _MOMENTUM_QUALITY_SORTS / _LOW_VOL_SORTS above are kept ONLY for building test
# fixtures (choosing a momentum vs low-vol sort_by_fn); the signature DECISION is
# the module's, never re-implemented here.


def _objective_signature(bpg, name: str):
    """Return a predicate `plan -> bool` delegating to the module's shared
    plan_matches_objective for the named objective (single source of truth)."""
    objective = bpg.Objective[name]
    return lambda plan: bpg.plan_matches_objective(plan, objective)


# A large in-universe membership set used across happy-path tests.
_BIG_UNIVERSE = frozenset(
    {f"TCK{i}" for i in range(40)} | {"SPY", "QQQ", "IWM", "TLT", "GLD", "AGG", "EFA"}
)


# ===========================================================================
# SECTION 0 — Module-level contract (named constants + Objective enum)
# ===========================================================================


def test_n_plans_per_objective_is_named_constant_equal_to_12(bpg):
    """AC-10: N_PLANS_PER_OBJECTIVE is a named module constant == 12 (not a magic number)."""
    assert hasattr(bpg, "N_PLANS_PER_OBJECTIVE")
    assert bpg.N_PLANS_PER_OBJECTIVE == 12


def test_objective_enum_has_exactly_the_four_canonical_values(bpg):
    """AC-8: the Objective enum is EXTENDED to four values, all four present."""
    names = {m.name for m in bpg.Objective}
    assert names == set(_OBJECTIVE_NAMES), f"Objective values must be exactly {_OBJECTIVE_NAMES}"


def test_provenance_constants_are_the_two_explicit_tags(bpg):
    """AC-13: explicit provenance field values exist as named constants."""
    assert bpg.PROVENANCE_BUILT_NEW == "built-new"
    assert bpg.PROVENANCE_ATLAS_SUGGESTED == "atlas-suggested"


# ===========================================================================
# SECTION 1 — AC-7: SDK tool-use parses into N well-formed build-plan objects
# ===========================================================================


def test_ac7_parses_tool_use_response_into_n_well_formed_plans(bpg):
    """AC-7: a mocked tool-use response carrying N STRUCTURALLY-DISTINCT plan dicts
    yields N plan objects, each conforming to the approved DSL shape.

    The three roots are deliberately distinct (different schemes/tickers/structure)
    so the AC-10 dedup does NOT fire — AC-7 tests PARSING of N plans, not dedup; the
    dedup behavior is covered by the dedicated AC-10 tests."""
    plans_in = [
        _plan(
            "diversify",
            _group(
                "sleeves",
                [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("equal", [_asset("TLT"), _asset("GLD")]),
                ],
            ),
            plan_id="p0",
            name="Plan 0",
        ),
        _plan(
            "diversify",
            _group(
                "sleeves",
                [
                    _weight("inverse_vol", [_asset("AGG"), _asset("TLT")], window_days=30),
                    _weight("equal", [_asset("EFA"), _asset("IWM")]),
                ],
            ),
            plan_id="p1",
            name="Plan 1",
        ),
        _plan(
            "diversify",
            _group(
                "sleeves",
                [
                    _filter("top", 2, "cumulative-return", 63, [_asset("SPY"), _asset("IWM")]),
                    _weight("equal", [_asset("GLD"), _asset("AGG")]),
                ],
            ),
            plan_id="p2",
            name="Plan 2",
        ),
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=3)
    assert len(result.plans) == 3
    for plan in result.plans:
        _assert_plan_shape(plan)
    assert result.reason is None


def test_ac7_each_parsed_plan_exposes_enumerable_tickers(bpg):
    """AC-7/DSL invariant: plan_tickers walks the plan and returns every referenced ticker."""
    root = _group(
        "sleeves",
        [
            _weight("equal", [_asset("SPY"), _asset("QQQ")]),
            _filter("top", 2, "cumulative-return", 63, [_asset("IWM"), _asset("EFA")]),
        ],
    )
    plans_in = [_plan("diversify", root)]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=1)
    tickers = bpg.plan_tickers(result.plans[0])
    assert tickers == {"SPY", "QQQ", "IWM", "EFA"}


def test_ac7_plan_tickers_collects_condition_and_binary_compound_tickers(bpg):
    """AC-7/AC-9 surface: plan_tickers reaches into if conditions AND binary_compound
    tickers[], excluding the '%' placeholder — this is the membership-validation walk."""
    root = {
        "kind": "if_compound",
        "condition": {
            "type": "binary_compound",
            "fn": "relative-strength-index",
            "tickers": ["NVDA", "AMD"],
            "comparator": "gt",
            "rhs": {"const": 70},
            "window": 14,
            "operator": "any",
        },
        "then": [_weight("equal", [_asset("SPY")])],
        "else": [_weight("equal", [_asset("TLT")])],
    }
    plans_in = [_plan("cut_drawdown", root)]
    universe = frozenset({"NVDA", "AMD", "SPY", "TLT"})
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), universe, n_plans=1)
    tickers = bpg.plan_tickers(result.plans[0])
    assert tickers == {"NVDA", "AMD", "SPY", "TLT"}
    assert "%" not in tickers


def test_ac7_every_returned_plan_is_tagged_built_new(bpg):
    """AC-13 (C2 slice): generator output carries provenance='built-new' on each plan."""
    plans_in = [
        _plan(
            "diversify",
            _group(
                "s",
                [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("equal", [_asset("TLT"), _asset("GLD")]),
                ],
            ),
            plan_id=f"p{i}",
        )
        for i in range(2)
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=2)
    assert result.plans, "expected at least one plan"
    for plan in result.plans:
        assert plan.get("provenance") == bpg.PROVENANCE_BUILT_NEW


def test_ac7_returned_plans_are_json_serializable(bpg):
    """AC-7/DSL invariant: build-plans are JSON-serializable (no python objects)."""
    plans_in = [
        _plan(
            "diversify",
            _group(
                "s",
                [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("inverse_vol", [_asset("TLT"), _asset("GLD")], window_days=30),
                ],
            ),
        )
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=1)
    # Round-trips through json without error.
    json.dumps(result.plans)


# ===========================================================================
# SECTION 2 — AC-8: each objective hard-shapes structure (FOUR distinct signatures)
# ===========================================================================


def test_ac8_diversify_plans_have_at_least_two_sleeves(bpg):
    """AC-8 diversify signature: >=2 sleeves at the root."""
    plans_in = [
        _plan(
            "diversify",
            _group(
                "sleeves",
                [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("inverse_vol", [_asset("TLT"), _asset("GLD")], window_days=30),
                    _weight("equal", [_asset("EFA"), _asset("AGG")]),
                ],
            ),
            plan_id=f"p{i}",
        )
        for i in range(3)
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=3)
    sig = _objective_signature(bpg, "diversify")
    assert result.plans
    assert all(sig(p) for p in result.plans), "every diversify plan must show >=2 sleeves"


def test_ac8_cut_drawdown_plans_have_regime_gate_or_inverse_vol(bpg):
    """AC-8 cut_drawdown signature: a regime gate (if/if_compound) OR inverse-vol weight."""
    plans_in = [
        # plan A: regime gate
        _plan(
            "cut_drawdown",
            _group(
                "g",
                [
                    _if_fixed(
                        "moving-average-price",
                        "SPY",
                        200,
                        "gt",
                        0,
                        then=[_weight("equal", [_asset("SPY"), _asset("QQQ")])],
                        els=[_weight("equal", [_asset("TLT"), _asset("AGG")])],
                    ),
                ],
            ),
            plan_id="pA",
        ),
        # plan B: inverse-vol
        _plan(
            "cut_drawdown",
            _weight("inverse_vol", [_asset("TLT"), _asset("AGG")], window_days=30),
            plan_id="pB",
        ),
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), _BIG_UNIVERSE, n_plans=2)
    sig = _objective_signature(bpg, "cut_drawdown")
    assert result.plans
    assert all(sig(p) for p in result.plans)


def test_ac8_lift_risk_adjusted_requires_momentum_quality_filter_not_bare_basket(bpg):
    """AC-8 lift_risk_adjusted signature (refinement B): a genuine momentum/quality FILTER.

    A bare specified-weight basket must NOT satisfy the signature — that is the
    inert-signature trap. The signature predicate requires a filter sorting on a
    momentum/quality indicator."""
    plans_in = [
        _plan(
            "lift_risk_adjusted",
            _filter(
                "top",
                3,
                "cumulative-return",
                126,
                [_asset(t) for t in ("SPY", "QQQ", "IWM", "EFA", "TLT")],
            ),
            plan_id=f"p{i}",
        )
        for i in range(3)
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(
            _objective(bpg, "lift_risk_adjusted"), _BIG_UNIVERSE, n_plans=3
        )
    sig = _objective_signature(bpg, "lift_risk_adjusted")
    assert result.plans
    assert all(sig(p) for p in result.plans)


def test_ac8_volatility_mitigation_requires_inverse_vol_or_low_vol_filter(bpg):
    """AC-8 volatility_mitigation signature: inverse-vol weighting OR a low/min-vol filter."""
    plans_in = [
        _plan(
            "volatility_mitigation",
            _weight("inverse_vol", [_asset("TLT"), _asset("AGG"), _asset("GLD")], window_days=30),
            plan_id="pA",
        ),
        _plan(
            "volatility_mitigation",
            _filter(
                "bottom",
                3,
                "standard-deviation-return",
                60,
                [_asset(t) for t in ("SPY", "QQQ", "IWM", "EFA", "TLT")],
            ),
            plan_id="pB",
        ),
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(
            _objective(bpg, "volatility_mitigation"), _BIG_UNIVERSE, n_plans=2
        )
    sig = _objective_signature(bpg, "volatility_mitigation")
    assert result.plans
    assert all(sig(p) for p in result.plans)


def test_ac8_bare_specified_basket_does_not_satisfy_lift_risk_adjusted_signature(bpg):
    """AC-8 refinement B (adversarial): a bare specified-weight basket fails the
    lift_risk_adjusted signature — proving the signature is not trivially satisfiable.

    This guards the inert-signature trap: if the generator emits a plain weighted
    basket for lift_risk_adjusted, the all(sig) assertion in the happy-path test
    must be able to FAIL. Here we feed exactly such a basket and require the
    signature predicate to reject it."""
    bare = _plan(
        "lift_risk_adjusted",
        _specified([(_asset("SPY"), 50.0), (_asset("QQQ"), 50.0)]),
        plan_id="bare",
    )
    sig = _objective_signature(bpg, "lift_risk_adjusted")
    assert sig(bare) is False, "a bare specified-weight basket must NOT satisfy the signature"


def test_ac8_four_objective_signatures_are_mutually_distinguishable(bpg):
    """AC-8: the four signatures are mutually distinguishable — a plan engineered to
    satisfy ONE objective's signature must not silently satisfy a structurally-distinct
    sibling. A pure single-sleeve momentum filter satisfies lift_risk_adjusted but NOT
    the diversify (>=2 sleeve) signature."""
    mom_filter_plan = _plan(
        "lift_risk_adjusted",
        _filter("top", 2, "cumulative-return", 63, [_asset("SPY"), _asset("QQQ"), _asset("IWM")]),
        plan_id="m",
    )
    assert _objective_signature(bpg, "lift_risk_adjusted")(mom_filter_plan) is True
    assert _objective_signature(bpg, "diversify")(mom_filter_plan) is False


# ===========================================================================
# SECTION 2b — AC-8 ENFORCEMENT (PM decision B): the generator FILTERS by signature
# ===========================================================================
#
# The generator does not merely PROMPT for objective-shaped plans — it ENFORCES the
# objective signature as an admission guarantee: after prune + dedup, any plan that
# does NOT satisfy plan_matches_objective(plan, objective) is DROPPED. The mocked SDK
# lets us feed a deliberate MIX of conforming + non-conforming plans and assert the
# generator admits ONLY the conforming ones. The signature predicate is the module's
# shared plan_matches_objective (single source of truth) — the same code the tests
# assert with, so the filter and the assertion cannot drift.


def _conforming_plan(name_obj: str, plan_id: str) -> dict:
    """Build a plan that DOES satisfy the named objective's signature."""
    if name_obj == "diversify":
        root = _group(
            "s",
            [
                _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                _weight("equal", [_asset("TLT"), _asset("GLD")]),
            ],
        )
    elif name_obj == "cut_drawdown":
        root = _weight("inverse_vol", [_asset("TLT"), _asset("AGG")], window_days=30)
    elif name_obj == "lift_risk_adjusted":
        root = _filter(
            "top", 2, "cumulative-return", 126, [_asset("SPY"), _asset("QQQ"), _asset("IWM")]
        )
    elif name_obj == "volatility_mitigation":
        root = _weight("inverse_vol", [_asset("TLT"), _asset("AGG"), _asset("GLD")], window_days=30)
    else:
        raise ValueError(name_obj)
    return _plan(name_obj, root, plan_id=plan_id)


def _nonconforming_plan(name_obj: str, plan_id: str) -> dict:
    """Build a well-formed plan that does NOT satisfy the named objective's signature.

    Each is a structurally-valid DSL plan that fails ONLY the objective's signature:
    - diversify: a single-sleeve equal-weight basket (1 sleeve, needs >=2)
    - cut_drawdown: a plain equal-weight basket (no regime gate, no inverse-vol)
    - lift_risk_adjusted: a plain equal-weight basket (no momentum/quality filter)
    - volatility_mitigation: a plain equal-weight basket (no inverse-vol, no low-vol filter)
    """
    if name_obj == "diversify":
        root = _weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("IWM")])  # 1 sleeve
    else:
        # A plain equal-weight basket conforms to NONE of the other three signatures.
        root = _weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("IWM")])
    return _plan(name_obj, root, plan_id=plan_id)


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_ac8_enforce_nonconforming_plan_is_filtered_out(bpg, obj_name):
    """AC-8 (B): a non-conforming plan is DROPPED by the generator; the conforming
    sibling in the same batch is kept. Admission is a signature GUARANTEE, not faith
    in the SDK prompt."""
    conforming = _conforming_plan(obj_name, "keep")
    nonconforming = _nonconforming_plan(obj_name, "drop")
    client = _client_returning_plans([nonconforming, conforming])
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, obj_name), _BIG_UNIVERSE, n_plans=12)
    admitted_ids = {p["plan_id"] for p in result.plans}
    assert "drop" not in admitted_ids, f"{obj_name}: non-conforming plan must be filtered out"
    assert "keep" in admitted_ids, f"{obj_name}: conforming plan must be admitted"


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_ac8_enforce_every_admitted_plan_satisfies_its_objective_signature(bpg, obj_name):
    """AC-8 (B) invariant: EVERY admitted plan satisfies its objective signature, even
    when the SDK returns a mix of conforming + non-conforming plans."""
    plans_in = [
        _conforming_plan(obj_name, "c1"),
        _nonconforming_plan(obj_name, "n1"),
        _conforming_plan(obj_name, "c2"),  # distinct id; structure may dedup with c1
        _nonconforming_plan(obj_name, "n2"),
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, obj_name), _BIG_UNIVERSE, n_plans=12)
    sig = _objective_signature(bpg, obj_name)
    assert result.plans, f"{obj_name}: at least one conforming plan must survive"
    assert all(sig(p) for p in result.plans), (
        f"{obj_name}: every admitted plan must satisfy its objective signature"
    )


def test_ac8_enforce_mutual_distinguishability_admit_under_x_filter_under_y(bpg):
    """AC-8 (B) mutual-distinguishability: a plan conforming to objective X but NOT to a
    structurally-distinct sibling Y is ADMITTED when the run targets X and FILTERED OUT
    when the run targets Y. We use a single-sleeve momentum filter — it conforms to
    lift_risk_adjusted (momentum filter) but NOT to diversify (needs >=2 sleeves)."""
    mom_filter_root = _filter(
        "top", 2, "cumulative-return", 63, [_asset("SPY"), _asset("QQQ"), _asset("IWM")]
    )
    # Admitted under lift_risk_adjusted.
    plan_x = _plan("lift_risk_adjusted", mom_filter_root, plan_id="x")
    client_x = _client_returning_plans([plan_x])
    with _patch_client(bpg, client_x):
        result_x = bpg.generate_build_plans(
            _objective(bpg, "lift_risk_adjusted"), _BIG_UNIVERSE, n_plans=12
        )
    assert {p["plan_id"] for p in result_x.plans} == {"x"}, (
        "must be admitted under lift_risk_adjusted"
    )

    # Same structure, filtered out under diversify (1 sleeve < 2).
    plan_y = _plan("diversify", mom_filter_root, plan_id="y")
    client_y = _client_returning_plans([plan_y])
    with _patch_client(bpg, client_y):
        result_y = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert "y" not in {p["plan_id"] for p in result_y.plans}, "must be filtered out under diversify"


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_ac8_enforce_all_nonconforming_degrades_to_honest_empty(bpg, obj_name):
    """AC-8 (B) + AC-11/AC-23: when the SDK returns ONLY non-conforming plans, the
    generator returns an honest empty result — never fabricates, never silently emits a
    non-conforming plan. result.plans == [] and a reason is surfaced (limited result)."""
    plans_in = [_nonconforming_plan(obj_name, f"n{i}") for i in range(5)]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, obj_name), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == [], f"{obj_name}: all-non-conforming must yield empty plans"
    # Honest degradation: a non-None reason explains the empty/limited result.
    assert isinstance(result.reason, str) and result.reason, (
        f"{obj_name}: empty-after-filter must surface an honest reason (AC-11/AC-23)"
    )


def test_ac8_enforce_filter_runs_after_prune_and_dedup(bpg):
    """AC-8 (B) ordering: the signature filter runs AFTER prune + dedup. A diversify plan
    that has >=2 sleeves ONLY because of an off-universe sleeve is, after pruning that
    sleeve away, single-sleeve and non-conforming — so it must be FILTERED OUT (not
    admitted on its pre-prune shape)."""
    # Root has 2 sleeves; one sleeve's only asset is off-universe and gets pruned,
    # collapsing the plan to a single in-universe sleeve -> fails diversify >=2.
    root = _group(
        "s",
        [
            _weight("equal", [_asset("SPY"), _asset("QQQ")]),  # in-universe sleeve
            _weight("equal", [_asset("ZZZZ")]),  # off-universe-only sleeve -> pruned away
        ],
    )
    collapses = _plan("diversify", root, plan_id="collapses")
    genuinely_two = _conforming_plan("diversify", "genuine")
    universe = frozenset({"SPY", "QQQ", "TLT", "GLD"})  # excludes ZZZZ
    client = _client_returning_plans([collapses, genuinely_two])
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), universe, n_plans=12)
    admitted_ids = {p["plan_id"] for p in result.plans}
    assert "collapses" not in admitted_ids, (
        "a plan that collapses to 1 sleeve after prune must fail the post-prune signature filter"
    )
    assert "genuine" in admitted_ids


# ===========================================================================
# SECTION 3 — AC-9: membership validation prunes off-universe tickers (refinement A)
# ===========================================================================


def test_ac9_off_universe_ticker_pruned_when_in_universe_siblings_remain(bpg):
    """AC-9 refinement A(a): an off-universe ticker is pruned when in-universe siblings
    remain; the surviving plan keeps the in-universe tickers and drops the off-universe one.

    Uses a 2-sleeve diversify root so the plan also satisfies the AC-8 diversify
    enforcement filter — ZZZZ is pruned from the first sleeve, both sleeves keep
    in-universe siblings, so >=2 sleeves survive and the plan is admitted."""
    # ZZZZ is off-universe; SPY/QQQ/IWM/TLT are in-universe siblings across two sleeves.
    root = _group(
        "g",
        [
            _weight("equal", [_asset("SPY"), _asset("ZZZZ"), _asset("QQQ")]),
            _weight("equal", [_asset("IWM"), _asset("TLT")]),
        ],
    )
    plans_in = [_plan("diversify", root, plan_id="p")]
    universe = frozenset({"SPY", "QQQ", "IWM", "TLT"})  # excludes ZZZZ
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), universe, n_plans=1)
    # The plan survives (siblings remain in both sleeves) and ZZZZ is gone.
    assert result.plans, "plan with in-universe siblings must survive the prune"
    surviving_tickers = set()
    for plan in result.plans:
        surviving_tickers |= bpg.plan_tickers(plan)
    assert "ZZZZ" not in surviving_tickers
    assert {"SPY", "QQQ", "IWM", "TLT"} <= surviving_tickers


def test_ac9_degenerate_prune_rejects_plan_instead_of_emitting_broken(bpg):
    """AC-9 refinement A(b): when pruning the only off-universe child would leave a node
    empty/degenerate, the PLAN IS REJECTED — never emitted broken."""
    # The weight node's ONLY child is off-universe -> pruning empties it -> reject.
    degenerate = _plan(
        "diversify",
        _weight("equal", [_asset("ZZZZ")]),
        plan_id="degenerate",
    )
    # A second, fully-valid plan so the run still returns something.
    good = _plan(
        "diversify",
        _group(
            "g",
            [
                _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                _weight("equal", [_asset("IWM"), _asset("EFA")]),
            ],
        ),
        plan_id="good",
    )
    plans_in = [degenerate, good]
    universe = frozenset({"SPY", "QQQ", "IWM", "EFA"})  # excludes ZZZZ
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), universe, n_plans=2)
    plan_ids = {p["plan_id"] for p in result.plans}
    assert "degenerate" not in plan_ids, "degenerate-prune plan must be rejected, not emitted"
    assert "good" in plan_ids, "the valid plan must survive"
    # And no admitted plan references the off-universe ticker.
    for plan in result.plans:
        assert "ZZZZ" not in bpg.plan_tickers(plan)


def test_ac9_off_universe_ticker_in_if_condition_pruned_or_plan_rejected(bpg):
    """AC-9: an off-universe ticker referenced in an if condition (not just an asset
    leaf) is handled — it never survives in the admitted output."""
    root = _if_fixed(
        "moving-average-price",
        "ZZZZ",
        200,
        "gt",
        0,
        then=[_weight("equal", [_asset("SPY"), _asset("QQQ")])],
        els=[_weight("equal", [_asset("IWM"), _asset("EFA")])],
    )
    plans_in = [_plan("cut_drawdown", root, plan_id="cond")]
    universe = frozenset({"SPY", "QQQ", "IWM", "EFA"})  # excludes ZZZZ (the signal)
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), universe, n_plans=1)
    for plan in result.plans:
        assert "ZZZZ" not in bpg.plan_tickers(plan)


def test_ac9_off_universe_rhs_ticker_in_ticker_comparison_if_handled(bpg):
    """AC-9 gap (Revise): an off-universe ticker as the RHS of a ticker-comparison
    `if` condition (not the lhs signal, not a fixed value) is also handled — it never
    survives in the admitted output. The condition signal is a reference that cannot
    be safely repaired by pruning, so the plan is rejected."""
    root = _if_ticker_cmp(
        "moving-average-price",
        "SPY",  # lhs in-universe
        "moving-average-price",
        "ZZZZ",  # rhs off-universe
        50,
        "gt",
        then=[_weight("equal", [_asset("SPY"), _asset("QQQ")])],
        els=[_weight("equal", [_asset("IWM"), _asset("EFA")])],
    )
    good = _plan(
        "cut_drawdown",
        _weight("inverse_vol", [_asset("TLT"), _asset("AGG")], window_days=30),
        plan_id="good",
    )
    plans_in = [_plan("cut_drawdown", root, plan_id="rhscmp"), good]
    universe = frozenset({"SPY", "QQQ", "IWM", "EFA", "TLT", "AGG"})  # excludes ZZZZ
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), universe, n_plans=2)
    plan_ids = {p["plan_id"] for p in result.plans}
    assert "rhscmp" not in plan_ids, "off-universe rhs signal must reject the plan"
    assert "good" in plan_ids
    for plan in result.plans:
        assert "ZZZZ" not in bpg.plan_tickers(plan)


def test_ac9_admitted_plan_does_not_alias_sdk_input_nodes(bpg):
    """AC-9 gap (Revise): the membership-validation step must NOT return admitted plans
    that ALIAS the SDK input node objects. A later compile/repair step (Component 3)
    mutates the admitted tree; if it aliased the SDK response, that would corrupt the
    caller's input. The symphony_schema constructors deep-copy for exactly this reason.

    We assert the admitted plan's asset leaves are not the SAME objects as the input's,
    by mutating an admitted asset and confirming the input plan is unchanged.

    Uses a 2-sleeve diversify root so the plan also satisfies the AC-8 diversify
    enforcement filter (the aliasing property is independent of the objective shape)."""
    in_asset_a = _asset("SPY")
    in_asset_b = _asset("QQQ")
    root = _group(
        "g",
        [
            _weight("equal", [in_asset_a]),
            _weight("equal", [in_asset_b]),
        ],
    )
    plan_in = _plan("diversify", root, plan_id="alias")
    plans_in = [plan_in]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(
            _objective(bpg, "diversify"), frozenset({"SPY", "QQQ"}), n_plans=1
        )
    assert result.plans, "in-universe plan must survive"
    admitted = result.plans[0]
    # Find an admitted asset leaf and mutate its ticker.
    admitted_assets = [n for n in _iter_nodes(admitted["root"]) if n.get("kind") == "asset"]
    assert admitted_assets, "expected at least one admitted asset leaf"
    admitted_assets[0]["ticker"] = "MUTATED"
    # The SDK input's asset objects must be untouched (no aliasing).
    assert in_asset_a["ticker"] == "SPY"
    assert in_asset_b["ticker"] == "QQQ"


def test_ac9_in_universe_only_plan_passes_through_unchanged_in_structure(bpg):
    """AC-9: a plan whose every ticker is in-universe is admitted with its structure
    intact (no spurious pruning of valid tickers)."""
    root = _group(
        "g",
        [
            _weight("equal", [_asset("SPY"), _asset("QQQ")]),
            _filter("top", 2, "cumulative-return", 63, [_asset("IWM"), _asset("EFA")]),
        ],
    )
    plans_in = [_plan("diversify", root, plan_id="clean")]
    universe = frozenset({"SPY", "QQQ", "IWM", "EFA"})
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), universe, n_plans=1)
    assert len(result.plans) == 1
    assert bpg.plan_tickers(result.plans[0]) == {"SPY", "QQQ", "IWM", "EFA"}


# ===========================================================================
# SECTION 4 — AC-10: diversity guarantee + dedup (refinement C)
# ===========================================================================


def test_ac10_run_requests_up_to_n_plans_default(bpg):
    """AC-10: when n_plans is not overridden, the run requests up to the named default (12)."""
    # Build 12 structurally-DISTINCT plans so none are deduped.
    plans_in = []
    for i in range(12):
        # vary sleeve count / scheme so each is structurally distinct
        sleeves = [_weight("equal", [_asset(f"TCK{2 * i}"), _asset(f"TCK{2 * i + 1}")])]
        for k in range(i % 3 + 1):
            sleeves.append(
                _weight("inverse_vol", [_asset(f"TCK{30 + k}"), _asset("SPY")], window_days=30)
            )
        plans_in.append(_plan("diversify", _group(f"g{i}", sleeves), plan_id=f"p{i}"))
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE)
    # Default n_plans honored; up to 12 distinct plans admitted.
    assert len(result.plans) <= bpg.N_PLANS_PER_OBJECTIVE
    assert len(result.plans) >= 2


def test_ac10_structurally_identical_plans_are_deduped(bpg):
    """AC-10 refinement C: 12 structurally-identical plans collapse — admitted count is
    far below N_plans (clones are not independent FDR trials)."""
    clone_root = _group(
        "g",
        [
            _weight("equal", [_asset("SPY"), _asset("QQQ")]),
            _weight("equal", [_asset("TLT"), _asset("GLD")]),
        ],
    )
    # 12 clones with different plan_id/name but identical STRUCTURE + tickers.
    plans_in = [
        _plan("diversify", clone_root, plan_id=f"clone{i}", name=f"Clone {i}") for i in range(12)
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    # Dedup collapses identical structures to a single representative.
    assert len(result.plans) == 1, "structurally-identical plans must dedup to one"


def test_ac10_diversity_assertion_has_teeth_12_clones_are_not_diverse(bpg):
    """AC-10 refinement C (adversarial): a 12-clone batch must NOT be reported as a
    structurally-diverse batch. We assert the admitted set is not 12 identical shapes."""
    clone_root = _weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("IWM")])
    plans_in = [_plan("diversify", clone_root, plan_id=f"c{i}") for i in range(12)]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    # Diversity has teeth: structurally-identical clones do NOT yield 12 admitted plans.
    assert len(result.plans) < 12


def test_ac10_distinct_plans_are_not_collapsed(bpg):
    """AC-10: genuinely structurally-distinct plans are NOT over-deduped — three
    distinct shapes survive as three plans.

    Each plan is a 2-sleeve diversify group (so all three pass the AC-8 diversify
    enforcement filter), but the three differ in their second sleeve's scheme/shape,
    so they remain structurally distinct and must NOT dedup."""
    p1 = _plan(
        "diversify",
        _group(
            "g",
            [
                _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                _weight("equal", [_asset("TLT"), _asset("AGG")]),
            ],
        ),
        plan_id="a",
    )
    p2 = _plan(
        "diversify",
        _group(
            "g",
            [
                _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                _weight("inverse_vol", [_asset("TLT"), _asset("AGG")], window_days=30),
            ],
        ),
        plan_id="b",
    )
    p3 = _plan(
        "diversify",
        _group(
            "g",
            [
                _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                _filter("top", 2, "cumulative-return", 63, [_asset("IWM"), _asset("EFA")]),
            ],
        ),
        plan_id="c",
    )
    client = _client_returning_plans([p1, p2, p3])
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert len(result.plans) == 3


def test_ac10_dedup_keeps_first_occurrence_deterministically(bpg):
    """AC-10 gap (Revise): when structurally-identical plans collapse, the FIRST
    occurrence is the representative kept (deterministic) — not an arbitrary one. We
    feed clones with ordered plan_ids and assert the surviving plan is the first."""
    clone_root = _group(
        "g",
        [
            _weight("equal", [_asset("SPY"), _asset("QQQ")]),
            _weight("equal", [_asset("TLT"), _asset("GLD")]),
        ],
    )
    plans_in = [
        _plan("diversify", clone_root, plan_id="first"),
        _plan("diversify", clone_root, plan_id="second"),
        _plan("diversify", clone_root, plan_id="third"),
    ]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert len(result.plans) == 1
    assert result.plans[0]["plan_id"] == "first"


def test_ac10_dedup_is_provenance_and_id_insensitive(bpg):
    """AC-10 gap (Revise): dedup keys on STRUCTURE only — two plans with identical root
    structure but different plan_id/name/provenance still collapse (the fingerprint must
    ignore volatile non-structural fields, else clones with different ids would survive
    and pad the FDR batch).

    The clone root is a 2-sleeve diversify group so both clones pass the AC-8
    enforcement filter; they then collapse to one on structure-only dedup."""
    clone_root = _group(
        "g",
        [
            _weight("equal", [_asset("SPY"), _asset("QQQ")]),
            _weight("equal", [_asset("IWM"), _asset("TLT")]),
        ],
    )
    p_a = _plan("diversify", clone_root, plan_id="AAA", name="Name One")
    p_b = _plan("diversify", clone_root, plan_id="BBB", name="Name Two")
    client = _client_returning_plans([p_a, p_b])
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert len(result.plans) == 1, "identical structure must dedup regardless of id/name"


def test_ac10_dedup_collapses_plans_differing_only_by_pruned_off_universe_ticker(bpg):
    """AC-10 + AC-9 interaction (Revise): two plans whose ONLY difference is an
    off-universe ticker that gets pruned away become structurally identical AFTER
    pruning — so they must dedup. (Dedup runs on the pruned plan, not the raw one.)

    Both plans are 2-sleeve diversify groups so they pass the AC-8 enforcement filter
    after pruning; plan_a's first sleeve carries an extra off-universe ZZZZ that is
    pruned, leaving both plans with identical {SPY,QQQ}+{IWM,TLT} structure -> dedup."""
    second_sleeve = _weight("equal", [_asset("IWM"), _asset("TLT")])
    # plan_a's first sleeve has an extra off-universe ZZZZ that will be pruned away.
    root_a = _group(
        "g",
        [_weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("ZZZZ")]), second_sleeve],
    )
    root_b = _group(
        "g",
        [_weight("equal", [_asset("SPY"), _asset("QQQ")]), second_sleeve],
    )
    plans_in = [
        _plan("diversify", root_a, plan_id="withzzz"),
        _plan("diversify", root_b, plan_id="without"),
    ]
    universe = frozenset({"SPY", "QQQ", "IWM", "TLT"})  # excludes ZZZZ
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), universe, n_plans=12)
    # After ZZZZ is pruned from plan_a, both roots are identical 2-sleeve groups -> dedup to 1.
    assert len(result.plans) == 1
    assert bpg.plan_tickers(result.plans[0]) == {"SPY", "QQQ", "IWM", "TLT"}


# ===========================================================================
# SECTION 5 — AC-11: D-1 honest degradation (never raises; no key/path leak)
# ===========================================================================

# A representative secret-shaped value we ensure never leaks into a reason string.
_FAKE_KEY = "sk-ant-SECRET-build-plan-key-1234567890"


def test_ac11_missing_api_key_degrades_to_empty_plans_with_reason(bpg):
    """AC-11: when _build_client raises (missing/invalid key), the generator returns an
    empty plan list + a non-empty reason — never raises."""
    with patch.object(bpg, "_build_client", side_effect=RuntimeError("ANTHROPIC_API_KEY not set")):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == []
    assert isinstance(result.reason, str) and result.reason


def test_ac11_reason_contains_only_exception_class_name_no_key_leak(bpg):
    """AC-11 / D-1 security: the reason string carries ONLY the exception class name —
    never the API key, never a file path, never the raw exception message."""

    # The class name itself is the ONE thing D-1 permits in the reason, so it must
    # not contain any of the forbidden substrings we assert against below (a class
    # named *Secret* would self-sabotage the "secret" check).
    class _BoomWithCredential(Exception):
        pass

    boom = _BoomWithCredential(f"failed using key={_FAKE_KEY} at C:/Users/leak/path.py")
    with patch.object(bpg, "_build_client", side_effect=boom):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == []
    assert _FAKE_KEY not in result.reason
    assert "secret" not in result.reason.lower()
    assert "C:/Users" not in result.reason and "path.py" not in result.reason
    # The exception class name IS allowed (D-1 contract).
    assert "_BoomWithCredential" in result.reason


def test_ac11_sdk_call_error_degrades_to_empty_plans(bpg):
    """AC-11: an error raised by messages.create (SDK failure) degrades to empty plans."""
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("SDK transport error")
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == []
    assert isinstance(result.reason, str) and result.reason
    assert "RuntimeError" in result.reason


def test_ac11_unparseable_tool_use_response_degrades_to_empty_plans(bpg):
    """AC-11: a malformed tool-use response (no tool_use block / wrong shape) degrades
    to empty plans with a reason — never raises, never returns garbage plans."""
    response = MagicMock()
    response.stop_reason = "end_turn"
    # A text block, not a tool_use block — the generator cannot extract plans.
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I refuse to use the tool."
    response.content = [text_block]
    client = MagicMock()
    client.messages.create.return_value = response
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == []
    assert isinstance(result.reason, str) and result.reason


def test_ac11_tool_use_input_with_non_list_plans_degrades_cleanly(bpg):
    """AC-11: a tool_use block whose `plans` payload is not a list degrades to empty
    plans — never raises, never iterates garbage."""
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [_tool_use_block({"plans": "not-a-list"})]
    client = MagicMock()
    client.messages.create.return_value = response
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert result.plans == []
    assert isinstance(result.reason, str) and result.reason


def test_ac11_empty_membership_set_degrades_cleanly(bpg):
    """AC-11/edge: an empty membership set means every ticker is off-universe — the run
    returns empty plans (every plan degenerate-pruned) without raising."""
    plans_in = [_plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))]
    client = _client_returning_plans(plans_in)
    with _patch_client(bpg, client):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), frozenset(), n_plans=1)
    assert result.plans == []


def test_ac11_never_returns_none_plans(bpg):
    """AC-11 invariant: result.plans is ALWAYS a list, never None, on every path."""
    with patch.object(bpg, "_build_client", side_effect=RuntimeError("x")):
        result = bpg.generate_build_plans(_objective(bpg, "diversify"), _BIG_UNIVERSE, n_plans=12)
    assert isinstance(result.plans, list)
