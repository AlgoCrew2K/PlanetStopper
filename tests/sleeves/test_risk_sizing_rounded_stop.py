"""RED — audit findings #5 (HIGH) and #19 (LOW, same fix): risk sizing uses
the UNROUNDED stop price while the broker leg is floored to the equity tick —
the position is sized against a tighter stop than the one that will actually
execute, so the worst-case loss exceeds the operator's declared risk budget
(repro: price $20, stop 0.075%, 1% risk -> $133.32 worst-case on a $100
budget, +33%). The overshoot fraction is tick/stop-distance — unbounded as
stops tighten. #19 is the same class for "clean" parameters: float-ulp error
(1.40 * 0.95 == 1.3299999999999998) makes ROUND_FLOOR drop a mathematically
exact on-tick stop a full tick.

Pinned contract (end-to-end through the real dispatch -> submit path, with
only the HTTP transport mocked): the stop the broker RECEIVES on the wire is
the effective stop, and

    qty * (entry_price - wire_stop_price) <= risk_pct * sleeve_equity

for every schema-legal (price, stop_pct, risk_pct) combination; and a
decimal-exact on-tick stop is submitted verbatim, never floored a tick lower.
Where the rounding happens (before sizing in actions.py per the verdict's
recommended fix, or Decimal end-to-end in alpaca_orders) is implementation
freedom — the wire truth is what these tests read.
"""

from __future__ import annotations

import decimal
import uuid
from unittest.mock import MagicMock, patch

import pytest

import database
import sleeves.rules.actions as actions
from tests.sleeves._alpaca_fixtures import load_order_fixture


def _make_ctx(*, price: float, capital_usd: float = 10_000.0) -> actions.ActionContext:
    sleeve_id = database.create_sleeve(
        f"stop-round-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
    )
    rule_id = database.create_sleeve_rule(sleeve_id, "stop-round-rule", json_doc="{}")
    return actions.ActionContext(
        sleeve_id=sleeve_id,
        rule_id=rule_id,
        symbol="SPY",
        price=price,
        sleeve_equity_usd=capital_usd,
        capital_usd=capital_usd,
        current_position_qty=0.0,
        turnover_used_usd=0.0,
        envelope={},
    )


def _ack_response() -> MagicMock:
    """A 200 broker ack for the real _request_with_retry transport layer —
    only requests.post is mocked; submit_bracket_order's own body-building
    (including its tick rounding) runs for real."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = load_order_fixture("bracket_new.json")
    return resp


def _dispatch_armed_buy_and_capture_wire_body(action: dict, ctx: actions.ActionContext) -> dict:
    with patch("sleeves.alpaca_orders.requests.post", return_value=_ack_response()) as mock_post:
        result = actions.dispatch_action(action, ctx=ctx, shadow=False)
    assert result.executed, (
        f"fixture sanity: the armed buy must actually submit an order "
        f"(refused_reason={result.refused_reason!r}) — if this fails the "
        f"budget assertion below would pass vacuously"
    )
    assert mock_post.called
    return mock_post.call_args.kwargs["json"]


class TestRiskBudgetHoldsAtTheWireStop:
    # (entry price, declared stop_loss_pct, declared risk_pct). Each combo is
    # schema-legal (stop/risk in (0,1)) and chosen so the UNROUNDED stop lands
    # off-tick: the first two by sub-tick stop distance, the third (finding
    # #19) by float-ulp error on a mathematically exact on-tick stop.
    @pytest.mark.parametrize(
        ("price", "stop_pct", "risk_pct"),
        [
            (20.0, 0.00075, 0.0005),
            (10.0, 0.0015, 0.001),
            (1.40, 0.05, 0.02),
        ],
    )
    def test_worst_case_loss_at_broker_stop_never_exceeds_risk_budget(
        self, price, stop_pct, risk_pct
    ):
        ctx = _make_ctx(price=price)
        action = {
            "type": "buy",
            "sizing": {"mode": "risk_pct", "risk_pct": risk_pct},
            "stop_loss_pct": stop_pct,
        }

        body = _dispatch_armed_buy_and_capture_wire_body(action, ctx)

        qty = float(body["qty"])
        wire_stop = float(body["stop_loss"]["stop_price"])
        assert qty >= 1.0, "fixture sanity: sizing must produce a nonzero order"
        assert wire_stop < price, "fixture sanity: a long's stop sits below entry"

        risk_budget = risk_pct * ctx.sleeve_equity_usd
        worst_case_loss = qty * (price - wire_stop)
        # rel 1e-9: the invariant is exact — qty is sized FROM the same stop
        # the broker executes — so only float representation error is allowed.
        assert worst_case_loss <= risk_budget * (1 + 1e-9), (
            f"risk budget overshoot (audit #5): qty {qty} was sized against "
            f"an unrounded stop, but the broker will execute the floored "
            f"stop {wire_stop} — worst-case loss ${worst_case_loss:.2f} vs "
            f"declared budget ${risk_budget:.2f} "
            f"({worst_case_loss / risk_budget - 1:+.1%}). The stop used for "
            f"sizing and the stop submitted to the broker must be the SAME "
            f"number."
        )


class TestExactOnTickStopIsNeverDroppedATick:
    def test_decimal_exact_stop_survives_to_the_wire_verbatim(self):
        """Finding #19: price 1.40, stop 5% -> exactly 1.33. IEEE-754 gives
        1.3299999999999998, and Decimal(str(float)) + ROUND_FLOOR then drops
        it to 1.32 — a full-tick stop giveaway for perfectly clean operator
        parameters. Sizing mode is 'shares' so this pins ONLY the price
        pipeline."""
        price, stop_pct = 1.40, 0.05
        ctx = _make_ctx(price=price)
        action = {
            "type": "buy",
            "sizing": {"mode": "shares", "shares": 2},
            "stop_loss_pct": stop_pct,
        }

        body = _dispatch_armed_buy_and_capture_wire_body(action, ctx)

        # The intended stop, computed in exact decimal arithmetic from the
        # operator's own parameters (both short decimals).
        expected_stop = decimal.Decimal(str(price)) * (1 - decimal.Decimal(str(stop_pct)))
        wire_stop = decimal.Decimal(body["stop_loss"]["stop_price"])
        assert wire_stop == expected_stop, (
            f"a mathematically exact on-tick stop ({expected_stop}) must be "
            f"submitted verbatim; got {wire_stop} — float-ulp error before "
            f"ROUND_FLOOR silently widens the stop a full tick (audit #19; "
            f"1,171 clean combos affected in the $1-$50 sweep)"
        )


class TestShadowSnapshotMatchesArmedSizing:
    def test_shadow_would_have_qty_equals_armed_wire_qty(self):
        """AC-6's evidence value depends on SHADOW fires reflecting the true
        would-have-been order. If GREEN rounds the stop for the armed path
        only, shadow snapshots overstate qty — this pins both paths to the
        same sizing input."""
        price, stop_pct, risk_pct = 20.0, 0.00075, 0.0005
        action = {
            "type": "buy",
            "sizing": {"mode": "risk_pct", "risk_pct": risk_pct},
            "stop_loss_pct": stop_pct,
        }

        shadow_ctx = _make_ctx(price=price)
        shadow_result = actions.dispatch_action(action, ctx=shadow_ctx, shadow=True)
        assert shadow_result.refused_reason is None, (
            f"fixture sanity: shadow sizing must succeed ({shadow_result.refused_reason!r})"
        )

        armed_ctx = _make_ctx(price=price)
        body = _dispatch_armed_buy_and_capture_wire_body(action, armed_ctx)

        # Exact equality of two identically-derived floats — any difference
        # means the two paths sized against different stops.
        assert shadow_result.would_have_qty == float(body["qty"]), (
            f"shadow would_have_qty ({shadow_result.would_have_qty}) must "
            f"equal the armed wire qty ({body['qty']}) for identical inputs — "
            f"otherwise SHADOW evidence misrepresents what arming will do"
        )
