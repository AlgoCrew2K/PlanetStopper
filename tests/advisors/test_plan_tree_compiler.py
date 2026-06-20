"""RED tests — Component 3: Plan->Tree Compiler (advisors/plan_tree_compiler.py).

Module under test: advisors.plan_tree_compiler (NEW — does not exist yet; the
ImportError / AttributeError is the first RED signal).

SCOPE: Component 3 ONLY.
  - AC-14 (this file): full-grammar deterministic compilation of a build-plan DSL
    into a Composer raw_value tree using ONLY existing advisors/symphony_schema.py
    constructors. Golden-fixture test per grammar construct; same plan ->
    byte-identical tree modulo fresh uuids.
  - AC-15 (validate+repair) and AC-16 (error-envelope split) live in
    test_plan_tree_compiler_repair.py.
  - AC-15 property invariant lives in test_plan_tree_compiler_property.py.
  - wt-marketcap disposition (AC-17) is BLOCKED at the producer (Composer deprecated
    market-cap weighting: POST /backtest -> HTTP 422 node-type-not-supported "Market
    cap weighting is no longer supported"; captured at
    tests/fixtures/strategy_builder/wt_marketcap_deprecated_envelope.json). The
    market_cap-scheme disposition tests live in test_plan_tree_compiler_repair.py
    once the PM confirms the AC-17 disposition; this file covers the THREE
    producer-accepted weighting schemes (equal / specified / inverse_vol).

CONTRACT SOURCES (no guessing):
  - The PM-approved build-plan DSL (.claude/tdd-handoff.md, the canonical
    generator<->compiler contract). The compiler is a 1:1 dispatch table: each
    NODE kind/scheme maps to exactly one symphony_schema constructor. The DSL
    NODE/CONDITION builders below are byte-identical to the Component-2 test DSL
    builders so the producer (Component 2 generator) and consumer (this compiler)
    agree on the wire shape.
  - advisors/symphony_schema.py constructors + validate_tree (imported LIVE so the
    structural assertions never hardcode the schema's field names / vocabulary).

ADVERSARIAL FOCUS (assert SHAPE / STRUCTURE / PRESENCE / DETERMINISM / VALIDITY —
NEVER hardcoded producer-computed values; the schema/math engine is never mocked):
  - Every construct compiles to the step token its constructor emits.
  - Every ticker in the DSL appears in the compiled tree (no silent drop on the
    valid path).
  - The compiled tree is validate_tree-clean (== []) for every construct.
  - Compilation is DETERMINISTIC: two compiles of the same plan are byte-identical
    after stripping the fresh uuid `id` keys.
  - The compiler dispatches each scheme to the matching constructor (a market_cap
    plan reaching THIS file is out of scope — market_cap disposition is the repair
    file).
"""

from __future__ import annotations

import copy

import pytest

from advisors import symphony_schema

# ---------------------------------------------------------------------------
# Module-under-test import guard. The module does not exist yet, so this import
# is the first RED failure. Tests reference `compiler` via the fixture below.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compiler():
    """Import and return the plan_tree_compiler module (RED until it exists)."""
    import advisors.plan_tree_compiler as _c  # noqa: PLC0415

    return _c


# ---------------------------------------------------------------------------
# DSL fixture builders — byte-identical to the Component-2 generator test DSL
# (tests/advisors/test_build_plan_generator.py). These produce VALID build-plan
# NODE dicts by SHAPE; the compiler consumes them. They never assert producer
# values — they are the inputs we fully control.
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


def _if_fixed(lhs_fn, lhs_ticker, window, comparator, fixed, then, els) -> dict:
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


def _binary_condition(lhs_fn, lhs_ticker, window, comparator, const) -> dict:
    return {
        "type": "binary",
        "lhs": {"fn": lhs_fn, "ticker": lhs_ticker, "window": window},
        "comparator": comparator,
        "rhs": {"const": const},
    }


def _binary_compound_condition(fn, tickers, comparator, const, window, operator="any") -> dict:
    return {
        "type": "binary_compound",
        "fn": fn,
        "tickers": list(tickers),
        "comparator": comparator,
        "rhs": {"const": const},
        "window": window,
        "operator": operator,
    }


def _compound_condition(operator, conditions) -> dict:
    return {"type": "compound", "operator": operator, "conditions": conditions}


def _if_compound(condition: dict, then: list, els: list) -> dict:
    return {"kind": "if_compound", "condition": condition, "then": then, "else": els}


def _plan(objective: str, root: dict, *, plan_id="p", name="Plan", rebalance="daily") -> dict:
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": name,
        "rebalance": rebalance,
        "root": root,
    }


# ---------------------------------------------------------------------------
# Tree-inspection helpers (test-local — NOT production). Walk a COMPILED Composer
# raw_value tree and collect step tokens / tickers. Used to assert the compiler
# routed each DSL kind to the right symphony_schema constructor by the step token
# that constructor emits (imported behaviour, not a hardcoded literal duplicated
# from production — we read what the constructors actually emit).
# ---------------------------------------------------------------------------


def _iter_tree_nodes(tree):
    """Iterative DFS over a compiled Composer tree yielding every node dict."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(children)


def _steps_in(tree) -> set[str]:
    return {n.get("step") for n in _iter_tree_nodes(tree) if isinstance(n.get("step"), str)}


def _strip_ids(tree):
    """Return a deep copy of a tree with every 'id' key removed (determinism check).

    The constructors emit fresh uuid4 ids on every call, so two compiles of the
    same plan differ ONLY in their ids. Stripping ids lets us assert byte-identity
    of the structural content.
    """
    if isinstance(tree, dict):
        return {k: _strip_ids(v) for k, v in tree.items() if k != "id"}
    if isinstance(tree, list):
        return [_strip_ids(v) for v in tree]
    return tree


def _compiled_tree(compiler, plan):
    """compile_plan(plan) and return the .tree, asserting a clean compile.

    The compiler returns a result object exposing .tree (the Composer raw_value
    dict, or None on drop) and .reason (str | None). On the valid path .tree must
    be a dict and .reason must be None.
    """
    result = compiler.compile_plan(plan)
    assert result.reason is None, f"unexpected compile reason: {result.reason!r}"
    assert isinstance(result.tree, dict), "compile_plan must return a tree dict on the valid path"
    return result.tree


# Reference step tokens the symphony_schema constructors emit. Imported BEHAVIOUR
# (we call the constructor and read its 'step') so the test cannot drift from the
# schema. NOT a hardcoded duplicate of a producer literal.
def _equal_step():
    return symphony_schema.make_weight_equal([])["step"]


def _specified_step():
    return symphony_schema.make_weight_specified([])["step"]


def _inverse_vol_step():
    return symphony_schema.make_inverse_vol([])["step"]


def _group_step():
    return symphony_schema.make_group("g", [])["step"]


def _filter_step():
    return symphony_schema.make_filter("top", 1, "cumulative-return", [], window=1)["step"]


def _root_step():
    return symphony_schema.make_root("n", "daily", [])["step"]


def _asset_step():
    return symphony_schema.make_asset("X")["step"]


def _if_step():
    return symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("current-price", "SPY", window=1), "gt", 1.0
        ),
        then_children=[symphony_schema.make_asset("SPY")],
        else_children=[symphony_schema.make_asset("TLT")],
    )["step"]


# ===========================================================================
# AC-14: full-grammar deterministic compilation, golden-fixture per construct.
# ===========================================================================


# --- Root + rebalance -------------------------------------------------------


def test_ac14_compiled_root_is_a_root_step_with_plan_rebalance(compiler):
    """The compiled tree's top node is a 'root' step carrying the plan's rebalance.

    rebalance is echoed from the DSL (a value we set), not a producer-computed
    quantity, so asserting equality is shape/passthrough, not a hardcoded producer
    output.
    """
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]), rebalance="weekly")
    tree = _compiled_tree(compiler, plan)
    assert tree.get("step") == _root_step()
    assert tree.get("rebalance") == "weekly"
    # validate_tree gates the compiled tree (AC-15 invariant, asserted here too).
    assert symphony_schema.validate_tree(tree) == []


def test_ac14_compiled_root_carries_the_required_description_field(compiler):
    """make_root emits the live-required empty 'description' (was HTTP 400). The
    compiler must go THROUGH make_root, so the field is present."""
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))
    tree = _compiled_tree(compiler, plan)
    assert "description" in tree


# --- Weight: equal ----------------------------------------------------------


def test_ac14_equal_weight_compiles_to_wt_cash_equal_with_both_tickers(compiler):
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))
    tree = _compiled_tree(compiler, plan)
    assert _equal_step() in _steps_in(tree)
    assert symphony_schema.extract_tickers(tree) == {"SPY", "QQQ"}
    assert symphony_schema.validate_tree(tree) == []


# --- Weight: specified ------------------------------------------------------


def test_ac14_specified_weight_compiles_to_wt_cash_specified_with_weights(compiler):
    """specified scheme -> make_weight_specified; each child carries a weight whose
    num is the DSL pct WE supplied (passthrough, not a producer value)."""
    plan = _plan(
        "diversify",
        _specified([(_asset("SPY"), 60), (_asset("TLT"), 40)]),
    )
    tree = _compiled_tree(compiler, plan)
    assert _specified_step() in _steps_in(tree)
    assert symphony_schema.extract_tickers(tree) == {"SPY", "TLT"}
    # The pcts we set must appear as weight numerators on the compiled children.
    nums = sorted(
        n["weight"]["num"]
        for n in _iter_tree_nodes(tree)
        if isinstance(n.get("weight"), dict) and "num" in n["weight"]
    )
    assert nums == [40, 60]
    assert symphony_schema.validate_tree(tree) == []


# --- Weight: inverse_vol ----------------------------------------------------


def test_ac14_inverse_vol_weight_compiles_to_wt_inverse_vol_with_window_days(compiler):
    """inverse_vol scheme -> make_inverse_vol, which emits the live-required
    window-days field (was HTTP 422)."""
    plan = _plan("cut_drawdown", _weight("inverse_vol", [_asset("SPY"), _asset("TLT")]))
    tree = _compiled_tree(compiler, plan)
    assert _inverse_vol_step() in _steps_in(tree)
    iv_nodes = [n for n in _iter_tree_nodes(tree) if n.get("step") == _inverse_vol_step()]
    assert iv_nodes, "expected a wt-inverse-vol node"
    assert all("window-days" in n for n in iv_nodes), "wt-inverse-vol must carry window-days"
    assert symphony_schema.validate_tree(tree) == []


def test_ac14_inverse_vol_honors_explicit_window_days_from_dsl(compiler):
    """When the DSL supplies window_days, the compiled wt-inverse-vol node carries
    THAT value (passthrough of a value WE set, not a producer output)."""
    plan = _plan(
        "cut_drawdown",
        _weight("inverse_vol", [_asset("SPY"), _asset("TLT")], window_days=45),
    )
    tree = _compiled_tree(compiler, plan)
    iv_nodes = [n for n in _iter_tree_nodes(tree) if n.get("step") == _inverse_vol_step()]
    assert iv_nodes
    assert all(n.get("window-days") == 45 for n in iv_nodes)


# --- Group (nested) ---------------------------------------------------------


def test_ac14_nested_group_compiles_to_nested_group_steps(compiler):
    """A group of groups compiles to nested 'group' steps; the group names we set
    appear; all tickers survive."""
    plan = _plan(
        "diversify",
        _group(
            "outer",
            [
                _group("eq-sleeve", [_weight("equal", [_asset("SPY"), _asset("QQQ")])]),
                _group("def-sleeve", [_weight("inverse_vol", [_asset("TLT"), _asset("GLD")])]),
            ],
        ),
    )
    tree = _compiled_tree(compiler, plan)
    assert _group_step() in _steps_in(tree)
    group_names = {n.get("name") for n in _iter_tree_nodes(tree) if n.get("step") == _group_step()}
    assert {"outer", "eq-sleeve", "def-sleeve"} <= group_names
    assert symphony_schema.extract_tickers(tree) == {"SPY", "QQQ", "TLT", "GLD"}
    assert symphony_schema.validate_tree(tree) == []


# --- Filter -----------------------------------------------------------------


def test_ac14_filter_compiles_to_filter_step_preserving_select_and_sort(compiler):
    """filter -> make_filter; select-fn/select-n/sort-by-fn echo the DSL values
    we supplied (passthrough), and the window lands in sort-by-fn-params."""
    plan = _plan(
        "lift_risk_adjusted",
        _filter(
            "top", 3, "cumulative-return", 63, [_asset("AAPL"), _asset("MSFT"), _asset("NVDA")]
        ),
    )
    tree = _compiled_tree(compiler, plan)
    filt_nodes = [n for n in _iter_tree_nodes(tree) if n.get("step") == _filter_step()]
    assert len(filt_nodes) == 1
    f = filt_nodes[0]
    assert f.get("select-fn") == "top"
    assert f.get("select-n") == 3
    assert f.get("sort-by-fn") == "cumulative-return"
    assert f.get("sort-by-fn-params", {}).get("window") == 63
    assert symphony_schema.extract_tickers(tree) == {"AAPL", "MSFT", "NVDA"}
    assert symphony_schema.validate_tree(tree) == []


# --- if (flat, fixed-value rhs) ---------------------------------------------


def test_ac14_if_fixed_compiles_to_if_with_true_and_else_branches(compiler):
    """if (fixed-value condition) -> make_if; produces a true-branch if-child with
    the lhs fn/comparator and an else if-child, both branches' tickers surviving."""
    plan = _plan(
        "cut_drawdown",
        _if_fixed(
            "relative-strength-index",
            "SPY",
            10,
            "gt",
            80,
            then=[_weight("equal", [_asset("UVXY")])],
            els=[_weight("equal", [_asset("SPY")])],
        ),
    )
    tree = _compiled_tree(compiler, plan)
    assert _if_step() in _steps_in(tree)
    # The compiled if-children carry the comparator we set.
    if_children = [n for n in _iter_tree_nodes(tree) if n.get("step") == "if-child"]
    assert any(c.get("comparator") == "gt" for c in if_children)
    assert any(c.get("is-else-condition?") for c in if_children)
    assert {"UVXY", "SPY"} <= symphony_schema.extract_tickers(tree)
    assert symphony_schema.validate_tree(tree) == []


def test_ac14_if_ticker_comparison_compiles_with_rhs_fn(compiler):
    """if (ticker-comparison condition) -> make_if with rhs-fixed-value?=False and a
    rhs-fn present (grammar §3.4 requires rhs-fn for ticker comparisons)."""
    plan = _plan(
        "lift_risk_adjusted",
        _if_ticker_cmp(
            "moving-average-price",
            "SPY",
            "moving-average-price",
            "QQQ",
            50,
            "gt",
            then=[_weight("equal", [_asset("SPY")])],
            els=[_weight("equal", [_asset("QQQ")])],
        ),
    )
    tree = _compiled_tree(compiler, plan)
    true_children = [
        n
        for n in _iter_tree_nodes(tree)
        if n.get("step") == "if-child" and not n.get("is-else-condition?")
    ]
    assert true_children
    assert all(c.get("rhs-fixed-value?") is False for c in true_children)
    assert all("rhs-fn" in c for c in true_children)
    assert symphony_schema.validate_tree(tree) == []


# --- if_compound: binary condition ------------------------------------------


def test_ac14_if_compound_binary_compiles_to_condition_block(compiler):
    """if_compound with a binary condition -> make_if_compound; the true if-child
    carries a 'condition' block (not flat lhs-fn fields)."""
    plan = _plan(
        "cut_drawdown",
        _if_compound(
            _binary_condition("relative-strength-index", "SPY", 14, "gt", 70),
            then=[_weight("equal", [_asset("UVXY")])],
            els=[_weight("equal", [_asset("SPY")])],
        ),
    )
    tree = _compiled_tree(compiler, plan)
    true_children = [
        n
        for n in _iter_tree_nodes(tree)
        if n.get("step") == "if-child" and not n.get("is-else-condition?")
    ]
    assert true_children
    assert all(isinstance(c.get("condition"), dict) for c in true_children)
    assert symphony_schema.validate_tree(tree) == []


# --- if_compound: binary_compound condition ---------------------------------


def test_ac14_if_compound_binary_compound_compiles_with_real_tickers(compiler):
    """if_compound binary_compound -> make_binary_compound_condition; the watched
    tickers we set are reachable via extract_tickers, the '%' placeholder is not."""
    plan = _plan(
        "cut_drawdown",
        _if_compound(
            _binary_compound_condition(
                "relative-strength-index", ["NVDA", "AMD"], "gt", 70, 14, operator="any"
            ),
            then=[_weight("equal", [_asset("SPY")])],
            els=[_weight("equal", [_asset("TLT")])],
        ),
    )
    tree = _compiled_tree(compiler, plan)
    all_tickers = symphony_schema.extract_tickers(tree)
    assert {"NVDA", "AMD", "SPY", "TLT"} <= all_tickers
    assert "%" not in all_tickers
    assert symphony_schema.validate_tree(tree) == []


# --- if_compound: compound condition (nested) -------------------------------


def test_ac14_if_compound_compound_condition_compiles_nested(compiler):
    """if_compound with a compound (any/all of N sub-conditions) -> nested
    make_compound_condition; all real watched tickers survive."""
    cond = _compound_condition(
        "all",
        [
            _binary_compound_condition("relative-strength-index", ["NVDA"], "gt", 70, 14),
            _binary_compound_condition("max-drawdown", ["AMD"], "lt", 20, 30),
        ],
    )
    plan = _plan(
        "cut_drawdown",
        _if_compound(
            cond,
            then=[_weight("equal", [_asset("SPY")])],
            els=[_weight("equal", [_asset("TLT")])],
        ),
    )
    tree = _compiled_tree(compiler, plan)
    all_tickers = symphony_schema.extract_tickers(tree)
    assert {"NVDA", "AMD", "SPY", "TLT"} <= all_tickers
    assert symphony_schema.validate_tree(tree) == []


# --- Full-grammar combined plan ---------------------------------------------


def _full_grammar_plan() -> dict:
    """A single plan exercising group nesting, all THREE producer-accepted weight
    schemes, a filter, a flat if, and an if_compound — the AC-14 'full grammar'
    sample. (market_cap is excluded — producer-deprecated, AC-17/repair file.)"""
    return _plan(
        "diversify",
        _group(
            "portfolio",
            [
                _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                _specified([(_asset("TLT"), 70), (_asset("GLD"), 30)]),
                _weight("inverse_vol", [_asset("IEF"), _asset("LQD")]),
                _filter("top", 2, "cumulative-return", 63, [_asset("AAPL"), _asset("MSFT")]),
                _if_fixed(
                    "relative-strength-index",
                    "SPY",
                    10,
                    "gt",
                    80,
                    then=[_weight("equal", [_asset("UVXY")])],
                    els=[_weight("equal", [_asset("VIXM")])],
                ),
                _if_compound(
                    _binary_compound_condition(
                        "relative-strength-index", ["NVDA", "AMD"], "gt", 70, 14
                    ),
                    then=[_weight("equal", [_asset("SOXL")])],
                    els=[_weight("equal", [_asset("SOXS")])],
                ),
            ],
        ),
    )


def test_ac14_full_grammar_plan_compiles_validate_clean(compiler):
    """The combined full-grammar plan compiles to a single validate_tree-clean tree
    exercising every producer-accepted construct."""
    tree = _compiled_tree(compiler, _full_grammar_plan())
    steps = _steps_in(tree)
    assert {
        _root_step(),
        _group_step(),
        _equal_step(),
        _specified_step(),
        _inverse_vol_step(),
        _filter_step(),
        _if_step(),
    } <= steps
    assert symphony_schema.validate_tree(tree) == []


def test_ac14_full_grammar_plan_preserves_every_ticker(compiler):
    """No ticker in the full-grammar plan is silently dropped on the valid path."""
    plan = _full_grammar_plan()
    tree = _compiled_tree(compiler, plan)
    expected = {
        "SPY",
        "QQQ",
        "TLT",
        "GLD",
        "IEF",
        "LQD",
        "AAPL",
        "MSFT",
        "UVXY",
        "VIXM",
        "NVDA",
        "AMD",
        "SOXL",
        "SOXS",
    }
    assert expected <= symphony_schema.extract_tickers(tree)


# ===========================================================================
# AC-14: determinism — same plan -> byte-identical tree modulo fresh uuids.
# ===========================================================================


def test_ac14_two_compiles_are_byte_identical_modulo_uuids(compiler):
    """Compiling the SAME plan twice yields trees identical after stripping ids.

    The only legitimate difference between two compiles is the fresh uuid4 `id`
    on every node (the constructors mint them). Stripping ids must collapse the
    two trees to identical structures (byte-identical via canonical JSON)."""
    plan = _full_grammar_plan()
    tree_a = _compiled_tree(compiler, plan)
    tree_b = _compiled_tree(compiler, plan)
    assert _strip_ids(tree_a) == _strip_ids(tree_b)


def test_ac14_compile_does_not_mutate_the_input_plan(compiler):
    """compile_plan is read-only w.r.t. its input — the plan dict is unchanged after
    compilation (the compiler deep-copies / builds fresh nodes)."""
    plan = _full_grammar_plan()
    before = copy.deepcopy(plan)
    compiler.compile_plan(plan)
    assert plan == before


def test_ac14_every_compiled_node_has_a_fresh_unique_id(compiler):
    """Determinism does NOT mean shared ids — every node in a single compiled tree
    carries a unique id (the constructors mint fresh uuid4s; duplicate ids are a
    validate_tree hard error)."""
    tree = _compiled_tree(compiler, _full_grammar_plan())
    ids = [n["id"] for n in _iter_tree_nodes(tree) if "id" in n]
    assert len(ids) == len(set(ids)), "compiled tree has duplicate node ids"
    # And validate_tree (which flags duplicate ids) is clean.
    assert symphony_schema.validate_tree(tree) == []
