"""Property-based RED tests — Component 2 AC-9 membership invariant.

Module under test: advisors.build_plan_generator (NEW).

The load-bearing AC-9 invariant: for ANY generator output that the membership-
validation step ADMITS, no admitted plan references a ticker outside the membership
set. We assert it as a hypothesis property over randomly-shaped build-plans built
from a mix of in-universe and off-universe tickers.

Hypothesis (6.152.6 confirmed available) generates the inputs; the SDK is mocked
to "return" those inputs, and the membership set is a fixed in-universe set. The
property holds regardless of plan shape.

This test asserts SHAPE/PRESENCE only — it never asserts producer-computed values
and never mocks the math engine.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Module-under-test import guard (RED until the module exists).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bpg():
    import advisors.build_plan_generator as _bpg  # noqa: PLC0415

    return _bpg


# Fixed in-universe membership set. Off-universe tickers use a disjoint prefix.
_IN_UNIVERSE = frozenset({f"IN{i}" for i in range(12)})
_OFF_UNIVERSE_POOL = [f"OFF{i}" for i in range(12)]

_in_ticker = st.sampled_from(sorted(_IN_UNIVERSE))
_off_ticker = st.sampled_from(_OFF_UNIVERSE_POOL)
_any_ticker = st.one_of(_in_ticker, _off_ticker)


def _asset_strategy(ticker_strategy):
    return ticker_strategy.map(lambda t: {"kind": "asset", "ticker": t})


def _leaf_weight_strategy(ticker_strategy):
    """A weight node over 2..4 asset leaves (mix of in/off-universe)."""
    return st.lists(_asset_strategy(ticker_strategy), min_size=2, max_size=4).map(
        lambda assets: {"kind": "weight", "scheme": "equal", "children": assets}
    )


def _node_strategy(ticker_strategy, depth: int):
    """Recursive NODE strategy bounded by depth. Mixes weight / group / filter."""
    leaf = _leaf_weight_strategy(ticker_strategy)
    if depth <= 0:
        return leaf

    sub = _node_strategy(ticker_strategy, depth - 1)
    group = st.lists(sub, min_size=2, max_size=3).map(
        lambda kids: {"kind": "group", "name": "g", "children": kids}
    )
    filt = st.lists(_asset_strategy(ticker_strategy), min_size=2, max_size=4).map(
        lambda assets: {
            "kind": "filter",
            "select_fn": "top",
            "select_n": 2,
            "sort_by_fn": "cumulative-return",
            "window": 63,
            "children": assets,
        }
    )
    return st.one_of(leaf, group, filt)


def _plan_strategy():
    """A build-plan whose tickers are a mix of in-universe and off-universe."""
    return st.builds(
        lambda root, pid: {
            "plan_id": f"p{pid}",
            "objective": "diversify",
            "name": "Plan",
            "rebalance": "daily",
            "root": root,
        },
        _node_strategy(_any_ticker, depth=2),
        st.integers(min_value=0, max_value=9999),
    )


def _client_returning(plans):
    response = MagicMock()
    response.stop_reason = "tool_use"
    block = MagicMock()
    block.type = "tool_use"
    block.name = "emit_build_plans"
    block.input = {"plans": plans}
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


@settings(max_examples=120, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(plans=st.lists(_plan_strategy(), min_size=1, max_size=6))
def test_ac9_no_admitted_plan_references_an_off_universe_ticker(bpg, plans):
    """AC-9 property: for any generator output admitted, no admitted plan references a
    ticker outside the membership set. (Off-universe tickers are pruned, or the plan
    that would become degenerate is rejected — never an admitted off-universe ticker.)"""
    client = _client_returning(plans)
    with patch.object(bpg, "_build_client", return_value=client):
        result = bpg.generate_build_plans(
            bpg.Objective["diversify"], _IN_UNIVERSE, n_plans=len(plans)
        )
    # Never raises; plans is always a list.
    assert isinstance(result.plans, list)
    for plan in result.plans:
        admitted = bpg.plan_tickers(plan)
        off = admitted - _IN_UNIVERSE
        assert not off, f"admitted plan {plan.get('plan_id')!r} references off-universe {off}"


@settings(max_examples=80, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(plans=st.lists(_plan_strategy(), min_size=1, max_size=6))
def test_ac9_property_generator_never_raises_on_arbitrary_plans(bpg, plans):
    """AC-11 corollary over the property domain: arbitrary plan shapes never raise."""
    client = _client_returning(plans)
    with patch.object(bpg, "_build_client", return_value=client):
        result = bpg.generate_build_plans(
            bpg.Objective["diversify"], _IN_UNIVERSE, n_plans=len(plans)
        )
    assert isinstance(result.plans, list)
    assert result.reason is None or isinstance(result.reason, str)
