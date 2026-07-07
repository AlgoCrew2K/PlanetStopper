"""
Schema-derived Alpaca v2 fixture loader + runtime shape validator.

Fixture-provenance (Gate-1 hard rule): the `/api-fixture` skill refuses any
write-verb method name (submit/cancel/place_order/liquidate/delete), so order
lifecycle fixtures CANNOT be captured live for this cycle. These fixtures are
schema-derived instead — hand-built from Alpaca's publicly documented v2
`Order`, `Account`, and `Position` object schemas (docs.alpaca.markets),
never co-designed against the sleeves parser. Every fixture is validated at
LOAD time against an independent shape check (`validate_alpaca_*_shape`)
defined here from the documented schema, not from whatever the parser
happens to read — that independence is what makes this satisfy Gate-1
(the validator would fail a fixture that "cheated" by matching the parser
instead of the real API contract).

This module is test support, not a test module itself (no `test_` prefix —
pyproject.toml's `python_files = ["test_*.py"]` will not collect it).
"""

from __future__ import annotations

import json
import pathlib

FIXTURES_DIR = pathlib.Path(__file__).parents[1] / "fixtures" / "alpaca" / "orders"

# ---------------------------------------------------------------------------
# Alpaca v2 Order object — documented required fields (docs.alpaca.markets/
# reference/getorderbyid-1). A "leg" (bracket child order) is itself a full
# Order object, so the same required-field set applies recursively.
# ---------------------------------------------------------------------------

ALPACA_ORDER_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "client_order_id",
        "created_at",
        "updated_at",
        "submitted_at",
        "filled_at",
        "canceled_at",
        "failed_at",
        "asset_id",
        "symbol",
        "asset_class",
        "qty",
        "filled_qty",
        "filled_avg_price",
        "order_class",
        "order_type",
        "type",
        "side",
        "time_in_force",
        "limit_price",
        "stop_price",
        "status",
        "extended_hours",
        "legs",
    }
)

ALPACA_ORDER_STATUS_VALUES: frozenset[str] = frozenset(
    {
        "new",
        "partially_filled",
        "filled",
        "done_for_day",
        "canceled",
        "expired",
        "replaced",
        "pending_cancel",
        "pending_replace",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "stopped",
        "rejected",
        "suspended",
        "calculated",
        "held",  # bracket/OTO child leg parked until the parent order fills
    }
)

ALPACA_ORDER_CLASS_VALUES: frozenset[str] = frozenset({"simple", "bracket", "oco", "oto"})

ALPACA_SIDE_VALUES: frozenset[str] = frozenset({"buy", "sell"})

# Position.side uses "long"/"short" — a distinct enum from Order.side ("buy"/"sell").
ALPACA_POSITION_SIDE_VALUES: frozenset[str] = frozenset({"long", "short"})

ALPACA_ACCOUNT_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "account_number",
        "status",
        "currency",
        "cash",
        "portfolio_value",
        "buying_power",
        "equity",
        "pattern_day_trader",
        "trading_blocked",
        "account_blocked",
        "shorting_enabled",
    }
)

ALPACA_POSITION_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "asset_id",
        "symbol",
        "asset_class",
        "qty",
        "avg_entry_price",
        "side",
        "market_value",
        "cost_basis",
        "unrealized_pl",
        "current_price",
    }
)


def validate_alpaca_order_shape(order: dict) -> list[str]:
    """Return a list of shape problems; empty list = valid Alpaca Order object.

    Independent of any sleeves production parser — derived only from the
    documented schema above.
    """
    problems: list[str] = []
    missing = ALPACA_ORDER_REQUIRED_FIELDS - order.keys()
    if missing:
        problems.append(f"missing required field(s): {sorted(missing)}")
    status = order.get("status")
    if status not in ALPACA_ORDER_STATUS_VALUES:
        problems.append(
            f"status {status!r} not in documented enum {sorted(ALPACA_ORDER_STATUS_VALUES)}"
        )
    order_class = order.get("order_class")
    if order_class not in ALPACA_ORDER_CLASS_VALUES:
        problems.append(f"order_class {order_class!r} not in {sorted(ALPACA_ORDER_CLASS_VALUES)}")
    side = order.get("side")
    if side not in ALPACA_SIDE_VALUES:
        problems.append(f"side {side!r} not in {sorted(ALPACA_SIDE_VALUES)}")
    legs = order.get("legs")
    if legs is not None:
        if not isinstance(legs, list):
            problems.append("legs must be a list or null")
        else:
            for i, leg in enumerate(legs):
                leg_problems = validate_alpaca_order_shape(leg)
                problems.extend(f"legs[{i}]: {p}" for p in leg_problems)
    return problems


def validate_alpaca_account_shape(account: dict) -> list[str]:
    problems: list[str] = []
    missing = ALPACA_ACCOUNT_REQUIRED_FIELDS - account.keys()
    if missing:
        problems.append(f"missing required field(s): {sorted(missing)}")
    return problems


def validate_alpaca_position_shape(position: dict) -> list[str]:
    problems: list[str] = []
    missing = ALPACA_POSITION_REQUIRED_FIELDS - position.keys()
    if missing:
        problems.append(f"missing required field(s): {sorted(missing)}")
    side = position.get("side")
    if side not in ALPACA_POSITION_SIDE_VALUES:
        problems.append(f"side {side!r} not in {sorted(ALPACA_POSITION_SIDE_VALUES)}")
    return problems


def _load_json(name: str) -> dict:
    path = FIXTURES_DIR / name
    assert path.exists(), f"fixture not found: {path}"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_order_fixture(name: str) -> dict:
    """Load an order fixture JSON file and assert it matches the documented shape."""
    data = _load_json(name)
    problems = validate_alpaca_order_shape(data)
    assert not problems, f"fixture {name} fails Alpaca Order shape validation: {problems}"
    return data


def load_account_fixture(name: str) -> dict:
    data = _load_json(name)
    problems = validate_alpaca_account_shape(data)
    assert not problems, f"fixture {name} fails Alpaca Account shape validation: {problems}"
    return data


def load_positions_fixture(name: str) -> list[dict]:
    data = _load_json(name)
    assert isinstance(data, list), f"fixture {name} must be a JSON array of positions"
    for i, pos in enumerate(data):
        problems = validate_alpaca_position_shape(pos)
        assert not problems, (
            f"fixture {name}[{i}] fails Alpaca Position shape validation: {problems}"
        )
    return data
