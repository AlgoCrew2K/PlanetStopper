"""Route-shaped test support for the managed-sleeves fix cycle (audit 2026-07-09).

WHY THIS MODULE EXISTS: the branch-accuracy audit proved every P2/P3 rule-engine
bug shipped behind GREEN tests because those tests baked ``sleeve_id`` (and a
pre-merged ``mode``) into the rule dict via tests/sleeves/rules/conftest.py's
``make_stored_rule`` — a shape the REAL create route never produces
(app.py create_sleeve_rule_route stores the raw client payload as json_doc;
``sleeve_id`` lives only in the URL and the sleeve_rules row). Everything here
builds state exactly the way production does:

  * ``valid_route_rule_payload`` is the operator's POST body — NO ``sleeve_id``,
    NO ``mode``, NO ``id`` key, ever. Adding one of those keys here (to make a
    test pass) defeats the entire purpose of this cycle and is FORBIDDEN.
  * ``create_sleeve_via_route`` / ``create_rule_via_route`` drive the real
    Flask routes, not database.* shortcuts.
  * ``tick_patches`` assembles the standard hermetic-broker patch set for
    driving ``run_sleeve_tick_for_all_sleeves`` (zero live network — house
    rule), mocking ONLY network and data-feed boundaries, never the engine.

This module is test support, not a test module (no ``test_`` prefix).
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

from sleeves import alpaca_orders

# Canonical market-open tick instant (Wednesday 2026-07-08, 10:31 ET) — the
# same convention tests/sleeves/test_tick_orchestrator.py established; NYSE is
# open, so a rule with the schema-default market_hours_only=True is evaluable.
MARKET_OPEN_NOW_UTC = datetime(2026, 7, 8, 14, 31, 0, tzinfo=UTC)

# The next NYSE trading day after MARKET_OPEN_NOW_UTC, same wall-clock time —
# for "auto re-arm next trading day" scenarios (AC-11).
NEXT_TRADING_DAY_NOW_UTC = datetime(2026, 7, 9, 14, 31, 0, tzinfo=UTC)


def valid_route_rule_payload(**overrides) -> dict:
    """The EXACT shape an operator POSTs to /api/sleeves/<id>/rules.

    Deliberately contains no ``sleeve_id``/``mode``/``id`` key — those are
    row-level facts owned by the URL and the sleeve_rules columns, and the
    production create route stores this payload verbatim as json_doc.
    Schema-valid per sleeves.rules.schema.validate_rule_doc (probed against
    the real validator). ``cooldown_sec`` is intentionally omitted (omission
    is schema-valid) so multi-tick scenarios a minute apart are not blocked
    by the pacing cooldown; tests that need a cooldown pass it explicitly.
    """
    payload = {
        "name": f"route-rule-{uuid.uuid4().hex[:8]}",
        "when": {"symbol": "SPY"},
        "if": {
            "op": "compare",
            "sense": "sma_20",
            "comparator": ">",
            "value": 100.0,
        },
        "then": [
            {
                "type": "buy",
                "sizing": {"mode": "risk_pct", "risk_pct": 0.01},
                "stop_loss_pct": 0.05,
            }
        ],
        "limits": {
            "max_fires_per_day": 10,
            "rearm_ticks": 3,
            "market_hours_only": True,
        },
    }
    payload.update(overrides)
    return payload


def create_sleeve_via_route(
    client, *, capital_usd: float = 10_000.0, envelope: dict | None = None
) -> int:
    """POST /api/sleeves exactly as the operator UI does; returns the new sleeve id."""
    resp = client.post(
        "/api/sleeves",
        json={
            "name": f"route-sleeve-{uuid.uuid4().hex[:8]}",
            "capital_usd": capital_usd,
            "envelope": envelope or {},
        },
    )
    assert resp.status_code == 200, (
        f"fixture sanity: sleeve create route failed "
        f"({resp.status_code}): {resp.get_data(as_text=True)[:300]!r}"
    )
    return resp.get_json()["sleeve"]["id"]


def create_rule_via_route(client, sleeve_id: int, payload: dict) -> int:
    """POST /api/sleeves/<id>/rules exactly as the operator UI does; returns rule id."""
    resp = client.post(f"/api/sleeves/{sleeve_id}/rules", json=payload)
    assert resp.status_code == 200, (
        f"fixture sanity: rule create route failed "
        f"({resp.status_code}): {resp.get_data(as_text=True)[:300]!r}"
    )
    return resp.get_json()["rule"]["id"]


def make_account(cash_usd: float) -> dict:
    """Minimal Alpaca Account double whose cash matches the caller's intended
    broker truth (shape mirrors tests/sleeves/_alpaca_fixtures.py's required
    account fields)."""
    return {
        "id": f"acct-{uuid.uuid4().hex}",
        "account_number": "PA0000ROUTETEST",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": str(cash_usd),
        "portfolio_value": str(cash_usd),
        "buying_power": str(cash_usd),
        "equity": str(cash_usd),
        "pattern_day_trader": False,
        "trading_blocked": False,
        "account_blocked": False,
        "shorting_enabled": False,
    }


def make_broker_position(symbol: str, qty: float, price: float) -> dict:
    """Minimal Alpaca Position double (documented required fields)."""
    return {
        "asset_id": f"asset-{uuid.uuid4().hex}",
        "symbol": symbol,
        "asset_class": "us_equity",
        "qty": str(qty),
        "avg_entry_price": str(price),
        "side": "long",
        "market_value": str(qty * price),
        "cost_basis": str(qty * price),
        "unrealized_pl": "0.00",
        "current_price": str(price),
    }


@contextmanager
def tick_patches(
    *,
    account_cash_usd: float,
    broker_positions: list[dict] | None = None,
    closes_by_symbol: dict[str, float] | None = None,
    bars_count: int = 30,
    get_order_result: alpaca_orders.OrderResult | None = None,
    cancel_order_result: alpaca_orders.OrderResult | None = None,
    submit_bracket_result: alpaca_orders.OrderResult | None = None,
):
    """Standard hermetic patch set for a real run_sleeve_tick_for_all_sleeves.

    Mocks ONLY the network/data boundaries (Alpaca REST doubles + the daily-bar
    feed); the orchestrator, runner, actions, sizing, envelope, ledger and DB
    all run for real. ``closes_by_symbol`` maps symbol -> constant close price;
    ``bars_count`` repeats it so a 20-bar SMA is well-defined.

    Yields a dict of the created mocks keyed by name so tests can assert
    call/no-call behavior (e.g. ``mocks["cancel_order"].assert_not_called()``).
    """
    positions = broker_positions or []
    closes = closes_by_symbol or {}

    def _fetch_bars(symbols, start, end, timeframe="1Day"):
        return {s: [{"c": closes[s]}] * bars_count for s in symbols if s in closes}

    with ExitStack() as stack:
        mocks = {
            "get_account": stack.enter_context(
                patch.object(
                    alpaca_orders,
                    "get_account",
                    return_value=alpaca_orders.OrderResult(
                        order=make_account(account_cash_usd), error=None
                    ),
                )
            ),
            "get_positions": stack.enter_context(
                patch.object(
                    alpaca_orders,
                    "get_positions",
                    return_value=alpaca_orders.OrderResult(order=positions, error=None),
                )
            ),
            "fetch_bars": stack.enter_context(
                patch("synthetic_history.fetch_bars", side_effect=_fetch_bars)
            ),
        }
        if get_order_result is not None:
            mocks["get_order"] = stack.enter_context(
                patch.object(alpaca_orders, "get_order", return_value=get_order_result)
            )
        if cancel_order_result is not None:
            mocks["cancel_order"] = stack.enter_context(
                patch.object(alpaca_orders, "cancel_order", return_value=cancel_order_result)
            )
        if submit_bracket_result is not None:
            mocks["submit_bracket_order"] = stack.enter_context(
                patch.object(
                    alpaca_orders, "submit_bracket_order", return_value=submit_bracket_result
                )
            )
        yield mocks
