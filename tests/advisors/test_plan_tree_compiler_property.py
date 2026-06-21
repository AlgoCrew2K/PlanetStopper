"""Property-based RED tests — Component 3 AC-15 compiler invariants.

Module under test: advisors.plan_tree_compiler (NEW).

The load-bearing AC-15 invariant: for ANY build-plan the compiler ADMITS (compiles
to a tree, reason=None), the resulting Composer raw_value tree is
symphony_schema.validate_tree-CLEAN (== []). A HARD-error tree must NEVER be
admitted — the compiler either repairs it or drops it cleanly. We assert this over
randomly-shaped build-plans drawn from the producer-accepted DSL grammar.

Second invariant (AC-15 / D-1): compile_plan NEVER raises over the property domain —
malformed / degenerate plans degrade to (tree=None, reason set), never an exception.

Hypothesis generates the build-plan inputs. The schema/math engine is NEVER mocked
(validate_tree runs for real); the backtest seam is a no-op success (the property is
about COMPILED-tree validity, independent of tradeability). This test asserts
SHAPE/VALIDITY only — never producer-computed values.

market_cap is excluded from the generated grammar here: it is producer-deprecated
(AC-17, see test_plan_tree_compiler_repair.py) and is a guaranteed DROP, so it would
never be ADMITTED — the admitted-implies-valid property is unaffected by its absence.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from advisors import symphony_schema


@pytest.fixture(scope="module")
def compiler():
    import advisors.plan_tree_compiler as _c  # noqa: PLC0415

    return _c


# A small in-universe ticker pool (membership is Component 2's concern — the
# compiler consumes already-validated plans, so all tickers here are "valid").
_TICKERS = sorted({f"T{i}" for i in range(8)})
_ticker = st.sampled_from(_TICKERS)


# ---------------------------------------------------------------------------
# No-op backtest seam: always succeeds. The property is about the COMPILED tree's
# validate_tree-cleanliness, not tradeability, so the seam returns success.
# ---------------------------------------------------------------------------


@dataclass
class _OkBacktest:
    error: str | None = None
    stats: dict | None = None


def _ok_backtest(raw_value, *a, **k):
    return _OkBacktest(error=None, stats={"sharpe": 0.0})


# ---------------------------------------------------------------------------
# Recursive DSL NODE strategy over the producer-accepted grammar.
# ---------------------------------------------------------------------------


def _asset_strategy():
    return _ticker.map(lambda t: {"kind": "asset", "ticker": t})


def _equal_strategy():
    return st.lists(_asset_strategy(), min_size=2, max_size=4).map(
        lambda kids: {"kind": "weight", "scheme": "equal", "children": kids}
    )


def _inverse_vol_strategy():
    return st.lists(_asset_strategy(), min_size=2, max_size=4).map(
        lambda kids: {"kind": "weight", "scheme": "inverse_vol", "children": kids}
    )


def _specified_strategy():
    return st.lists(_asset_strategy(), min_size=2, max_size=3).map(
        lambda kids: {
            "kind": "weight",
            "scheme": "specified",
            # pcts are values WE set; they need not sum to 100 (lint-only, not a
            # validate_tree hard error) — the property is about HARD-error freedom.
            "children": [{"node": n, "pct": 50} for n in kids],
        }
    )


def _filter_strategy():
    return st.lists(_asset_strategy(), min_size=2, max_size=4).map(
        lambda kids: {
            "kind": "filter",
            "select_fn": "top",
            "select_n": 2,
            "sort_by_fn": "cumulative-return",
            "window": 63,
            "children": kids,
        }
    )


def _leaf_strategy():
    return st.one_of(
        _equal_strategy(), _inverse_vol_strategy(), _specified_strategy(), _filter_strategy()
    )


def _node_strategy(depth: int):
    if depth <= 0:
        return _leaf_strategy()
    sub = _node_strategy(depth - 1)
    group = st.lists(sub, min_size=2, max_size=3).map(
        lambda kids: {"kind": "group", "name": "g", "children": kids}
    )
    # A flat fixed-value if over two leaf branches.
    if_node = st.tuples(sub, sub).map(
        lambda branches: {
            "kind": "if",
            "condition": {
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": _TICKERS[0],
                "window": 14,
                "comparator": "gt",
                "rhs": {"fixed": 70},
            },
            "then": [branches[0]],
            "else": [branches[1]],
        }
    )
    return st.one_of(_leaf_strategy(), group, if_node)


def _plan_strategy():
    return st.builds(
        lambda root, obj, reb: {
            "plan_id": "p",
            "objective": obj,
            "name": "Plan",
            "rebalance": reb,
            "root": root,
        },
        _node_strategy(3),
        st.sampled_from(
            ["diversify", "cut_drawdown", "lift_risk_adjusted", "volatility_mitigation"]
        ),
        st.sampled_from(sorted(symphony_schema.KNOWN_REBALANCE)),
    )


# ===========================================================================
# AC-15 property: any ADMITTED compiler output is validate_tree-clean.
# ===========================================================================


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(plan=_plan_strategy())
def test_ac15_property_admitted_tree_is_validate_clean(compiler, plan):
    """For any plan from the producer-accepted grammar, IF the compiler admits it
    (tree is not None / reason is None) THEN validate_tree(tree) == []."""
    result = compiler.compile_plan(plan, backtest_fn=_ok_backtest)
    if result.tree is not None:
        assert result.reason is None
        assert symphony_schema.validate_tree(result.tree) == []


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(plan=_plan_strategy())
def test_ac15_property_compile_never_raises(compiler, plan):
    """compile_plan never raises over the property domain — it returns a result with
    either a tree or a reason. (D-1 never-raises contract.)"""
    result = compiler.compile_plan(plan, backtest_fn=_ok_backtest)
    # A result object with the two-field contract is always returned.
    assert hasattr(result, "tree")
    assert hasattr(result, "reason")
    # Exactly one of (tree set, reason set) on the terminal state: a tree implies
    # no reason; a drop implies a reason.
    if result.tree is None:
        assert result.reason is not None
    else:
        assert result.reason is None


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    junk=st.one_of(
        st.none(),
        st.integers(),
        st.text(),
        st.lists(st.integers()),
        st.dictionaries(st.text(), st.integers()),
    )
)
def test_ac15_property_garbage_input_never_raises(compiler, junk):
    """Arbitrary garbage (non-build-plan inputs) degrades to a clean drop, never an
    exception (D-1). tree=None + a reason."""
    result = compiler.compile_plan(junk, backtest_fn=_ok_backtest)
    assert result.tree is None
    assert result.reason is not None
