"""Alpaca order client -- THE only order-capable module in the repo (AC-7/8/13/14/15).

This module is the single place permitted to construct Alpaca broker-order
requests (POST/DELETE /v2/orders, GET /v2/account, GET /v2/positions) and the
single place the live trading host string may appear. Every other production
module in the repo is denylisted from both by a whole-repo AST invariant
(tests/sleeves/test_containment_invariants.py).

Error handling contract (mirrors advisors/composer_backtest_client.BacktestResult)
-----------------------------------------------------------------------------
This module never raises on API or transport errors -- every failure mode
returns an ``OrderResult`` with ``order=None`` and ``error=<reason string>``.

D-1 redaction: ``error`` is built ONLY from ``type(exc).__name__`` for
transport failures, or ``f"HTTP {status_code}"`` for non-2xx responses --
never raw exception text (``str(exc)``) or response bodies, either of which
could carry request/response payload values (account figures, bearer tokens
echoed back by a misbehaving proxy, etc.).

Host gating (AC-13/AC-14)
--------------------------
``resolve_host`` is THE single designated host-selection function. It is a
pure function of two caller-supplied booleans -- it does not read the
environment itself, so paper is the floor and live is reachable only when
BOTH ``live_mode`` and ``live_keys_present`` are True. Computing
``live_keys_present`` (checking ``ALPACA_LIVE_KEY``/``ALPACA_LIVE_SECRET``
presence) and ``live_mode`` (sleeve status + ``SLEEVE_LIVE_EXECUTION``) is the
caller's (P2/P3 runner's) responsibility -- out of P1 scope.

Live / test separation
-----------------------
Tests patch ``requests.post``/``requests.get``/``requests.delete`` directly.
This module does not use a ``requests.Session`` internally so
``patch.object(alpaca_orders.requests, "post", ...)`` intercepts cleanly
(matches composer_backtest_client's own test convention).

No top-level network calls are made at import time.

Retry policy
------------
Exponential backoff across a bounded, explicit interval tuple
(``_BACKOFF_INTERVALS``). ``max_retries`` counts TOTAL attempts (not retries
after a first attempt) -- a persistent failure with ``max_retries=3`` makes
exactly 3 calls. 429 responses respect the ``Retry-After`` header when
present; otherwise the first backoff interval is used. A request ``Timeout``
is not retried (the server may still be processing the original request).
Every request carries an explicit timeout (``_REQUEST_TIMEOUT_S``) -- never
an unbounded call.
"""

from __future__ import annotations

import decimal
import os
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

# ---------------------------------------------------------------------------
# Host gating (AC-13/AC-14) -- the ONLY two lines in the repo permitted to
# contain the live host string outside resolve_host() itself.
# ---------------------------------------------------------------------------

_PAPER_HOST = "https://paper-api.alpaca.markets"
_LIVE_HOST = "https://api.alpaca.markets"

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

# Backoff intervals (seconds) indexed by 0-based attempt number.
_BACKOFF_INTERVALS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

# Hard ceiling on cumulative retry wait -- sum of all backoff intervals.
MAX_RETRY_WAIT_SECONDS: float = sum(_BACKOFF_INTERVALS)

# Explicit per-request HTTP timeout -- never rely on urllib3's default (None).
_REQUEST_TIMEOUT_S: float = 10.0

# HTTP status codes that are transient and warrant a retry (429 handled
# separately below so its Retry-After header can be honored).
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Default total-attempt count for read-only endpoints (get_order/get_account/
# get_positions/cancel_order) that do not expose max_retries to the caller.
_DEFAULT_READ_MAX_RETRIES: int = 4

# ---------------------------------------------------------------------------
# Bracket-leg price rounding to Alpaca's equity tick size (done-bar fix,
# 2026-07-08 -- PM's direct Alpaca repro: unrounded legs (e.g. tp=14.839,
# stop=12.8155) returned HTTP 422 code 42210000 "sub-penny increment does not
# fulfill minimum pricing criteria"; clean 2-decimal legs returned HTTP 200).
# Source: Alpaca's own documented minimum equity price increment -- 2
# decimals at/above $1.00, 4 decimals below (docs.alpaca.markets order
# limits). Rounded HERE, at the broker boundary, never in sleeves/rules/
# actions.py -- that module's own price math stays pure/unrounded.
# ---------------------------------------------------------------------------

_EQUITY_TICK_HIGH_PRICE_THRESHOLD: float = 1.00
_EQUITY_TICK_HIGH_PRICE_DECIMALS: int = 2
_EQUITY_TICK_LOW_PRICE_DECIMALS: int = 4


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderResult:
    """Structured result from a single Alpaca API call. Never raises.

    On success: ``order`` is the raw Alpaca response body (a dict for an
    Order/Account object, or a list for ``get_positions``), ``error`` is None.
    On failure: ``order`` is None, ``error`` is a non-empty D-1-safe reason.
    """

    order: dict | list | None
    error: str | None


# ---------------------------------------------------------------------------
# Host / credential resolution
# ---------------------------------------------------------------------------


def resolve_host(*, live_mode: bool, live_keys_present: bool) -> str:
    """THE single gated host-selection function (AC-13/AC-14).

    Pure function of its two boolean inputs -- reads no environment state
    itself. Returns the paper host UNLESS both ``live_mode`` and
    ``live_keys_present`` are True, in which case it returns the live host.
    Paper is the floor: live is structurally unreachable unless both gates
    are independently satisfied by the caller.
    """
    if live_mode and live_keys_present:
        return _LIVE_HOST
    return _PAPER_HOST


def _credential_headers(*, live_mode: bool, live_keys_present: bool) -> dict[str, str]:
    """Build the Alpaca auth headers for whichever host resolve_host would pick.

    Reads credentials from the environment only (D-1: never hardcoded).
    """
    if live_mode and live_keys_present:
        key = os.getenv("ALPACA_LIVE_KEY", "")
        secret = os.getenv("ALPACA_LIVE_SECRET", "")
    else:
        key = os.getenv("ALPACA_KEY", "")
        secret = os.getenv("ALPACA_SECRET", "")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


# ---------------------------------------------------------------------------
# Internal retry-wrapped request helper
# ---------------------------------------------------------------------------


def _backoff_delay(attempt: int) -> float:
    """Return the backoff delay for a 0-indexed attempt, clamped to the last interval."""
    return (
        _BACKOFF_INTERVALS[attempt] if attempt < len(_BACKOFF_INTERVALS) else _BACKOFF_INTERVALS[-1]
    )


def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict | None = None,
    max_retries: int = 4,
) -> OrderResult:
    """Execute one Alpaca HTTP call with bounded exponential backoff.

    ``max_retries`` is the TOTAL number of attempts (a persistent failure
    with ``max_retries=3`` makes exactly 3 calls). Never raises -- every
    failure mode is converted to an ``OrderResult`` with ``error`` built from
    ``type(exc).__name__`` (transport errors) or ``f"HTTP {status}"``
    (non-2xx responses) only -- never raw exception text or response bodies.
    """
    last_error = "unknown error"
    for attempt in range(max_retries):
        try:
            if method == "post":
                response = requests.post(
                    url, json=json_body, headers=headers, timeout=_REQUEST_TIMEOUT_S
                )
            elif method == "get":
                response = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT_S)
            elif method == "delete":
                response = requests.delete(url, headers=headers, timeout=_REQUEST_TIMEOUT_S)
            else:  # pragma: no cover — internal misuse guard, never hit by callers below
                raise ValueError(f"unsupported HTTP method: {method}")
        except requests.Timeout as exc:
            # Not worth retrying -- the server may still be processing the
            # original request (mirrors advisors/composer_backtest_client.py).
            return OrderResult(order=None, error=f"timeout: {type(exc).__name__}")
        except requests.RequestException as exc:
            last_error = f"transport error: {type(exc).__name__}"
            if attempt < max_retries - 1:
                time.sleep(_backoff_delay(attempt))
                continue
            return OrderResult(order=None, error=last_error)

        if response.status_code == 204:
            return OrderResult(order={}, error=None)

        if response.status_code == 200:
            try:
                return OrderResult(order=response.json(), error=None)
            except ValueError as exc:
                return OrderResult(order=None, error=f"invalid JSON response: {type(exc).__name__}")

        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            delay = float(retry_after_header) if retry_after_header else _BACKOFF_INTERVALS[0]
            last_error = "HTTP 429 rate limit exceeded"
            if attempt < max_retries - 1:
                time.sleep(delay)
                continue
            return OrderResult(order=None, error=last_error)

        if response.status_code in _RETRYABLE_HTTP_STATUSES and attempt < max_retries - 1:
            last_error = f"HTTP {response.status_code} (transient)"
            time.sleep(_backoff_delay(attempt))
            continue

        return OrderResult(order=None, error=f"HTTP {response.status_code}")

    return OrderResult(order=None, error=last_error)


# ---------------------------------------------------------------------------
# Public API -- order submission
# ---------------------------------------------------------------------------


def _round_to_equity_tick(price: float, *, rounding: str) -> float:
    """Round one bracket-leg price to Alpaca's equity tick size (2 decimals
    at/above $1.00, 4 decimals below -- see the module-level constants above
    for provenance).

    ``rounding`` is ``"floor"`` (never round up -- a long's protective
    stop_loss must never tighten closer to entry than sizing intended) or
    ``"nearest"`` (may go either way -- take_profit is not a protective
    boundary in the same tightening sense, so nearest-tick is correct).

    Uses decimal.Decimal quantization rather than float division + floor --
    IEEE-754 float division against a tick size like 0.01 is not exact
    (e.g. ``495.00 / 0.01`` is ``49499.999999999993``, not ``49500.0``),
    which would silently floor an already-valid price down a full tick.
    Constructing the Decimal from ``str(price)`` (not the float directly)
    preserves the decimal value the caller intended rather than its binary
    floating-point representation.
    """
    decimals = (
        _EQUITY_TICK_HIGH_PRICE_DECIMALS
        if price >= _EQUITY_TICK_HIGH_PRICE_THRESHOLD
        else _EQUITY_TICK_LOW_PRICE_DECIMALS
    )
    quantum = decimal.Decimal(1).scaleb(-decimals)
    mode = decimal.ROUND_FLOOR if rounding == "floor" else decimal.ROUND_HALF_UP
    quantized = decimal.Decimal(str(price)).quantize(quantum, rounding=mode)
    return float(quantized)


def submit_bracket_order(
    *,
    symbol: str,
    qty: float,
    side: str,
    take_profit_price: float,
    stop_loss_price: float,
    client_order_id: str | None = None,
    time_in_force: str = "day",
    live_mode: bool = False,
    live_keys_present: bool = False,
    max_retries: int = 4,
) -> OrderResult:
    """Submit a market-entry bracket order (entry + take-profit + stop-loss legs).

    AC-7: every entry defaults to a bracket so no position exists without a
    broker-side exit. The take-profit/stop-loss legs are held AT THE BROKER
    (AC-8) -- they survive engine downtime by construction.

    ``client_order_id``, when supplied, is forwarded verbatim to Alpaca's own
    ``client_order_id`` order field -- the caller (P2/P3 runner) mints this
    BEFORE the call so a lost HTTP response can still be recovered via
    ``get_order_by_client_order_id`` (lost-ack recovery pattern).

    Both leg prices are rounded to Alpaca's equity tick size before
    submission (done-bar fix, 2026-07-08): an unrounded sub-penny price
    (e.g. from risk-sizing math) is rejected by Alpaca with HTTP 422. This
    is the only rounding point -- sleeves/rules/actions.py's own price math
    stays pure/unrounded. stop_loss floors (never tightens the protective
    stop closer to entry than intended); take_profit rounds to nearest.
    """
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/orders"
    rounded_take_profit_price = _round_to_equity_tick(take_profit_price, rounding="nearest")
    rounded_stop_loss_price = _round_to_equity_tick(stop_loss_price, rounding="floor")
    body = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": time_in_force,
        "order_class": "bracket",
        "take_profit": {"limit_price": str(rounded_take_profit_price)},
        "stop_loss": {"stop_price": str(rounded_stop_loss_price)},
    }
    if client_order_id is not None:
        body["client_order_id"] = client_order_id
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry(
        "post", url, headers=headers, json_body=body, max_retries=max_retries
    )


def submit_trailing_stop_order(
    *,
    symbol: str,
    qty: float,
    side: str,
    trail_percent: float | None = None,
    trail_price: float | None = None,
    client_order_id: str | None = None,
    time_in_force: str = "day",
    live_mode: bool = False,
    live_keys_present: bool = False,
    max_retries: int = 4,
) -> OrderResult:
    """Submit a native Alpaca trailing-stop order, held at the broker (AC-8).

    Exactly one of ``trail_percent``/``trail_price`` should be supplied,
    matching Alpaca's own mutually-exclusive trail-spec fields.

    ``client_order_id``, when supplied, is forwarded verbatim -- see
    ``submit_bracket_order`` docstring for the lost-ack recovery rationale.
    """
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/orders"
    body: dict = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "trailing_stop",
        "time_in_force": time_in_force,
    }
    if trail_percent is not None:
        body["trail_percent"] = trail_percent
    if trail_price is not None:
        body["trail_price"] = trail_price
    if client_order_id is not None:
        body["client_order_id"] = client_order_id
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry(
        "post", url, headers=headers, json_body=body, max_retries=max_retries
    )


def submit_order(
    *,
    symbol: str,
    qty: float,
    side: str,
    order_type: str = "market",
    client_order_id: str | None = None,
    time_in_force: str = "day",
    live_mode: bool = False,
    live_keys_present: bool = False,
    max_retries: int = 4,
) -> OrderResult:
    """Submit a plain, non-bracket order (P2: sleeve rule "sell"/"go_to_cash"
    actions closing an existing position -- submit_bracket_order's mandatory
    take-profit/stop-loss legs are the wrong shape for a plain closing sell).

    Same conventions as submit_bracket_order/submit_trailing_stop_order: raw
    requests, D-1 error redaction, client_order_id lost-ack recovery, bounded
    retry/backoff.
    """
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/orders"
    body: dict = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": order_type,
        "time_in_force": time_in_force,
    }
    if client_order_id is not None:
        body["client_order_id"] = client_order_id
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry(
        "post", url, headers=headers, json_body=body, max_retries=max_retries
    )


def cancel_order(
    *, order_id: str, live_mode: bool = False, live_keys_present: bool = False
) -> OrderResult:
    """Cancel an open order by broker order id. Never raises."""
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/orders/{order_id}"
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry(
        "delete", url, headers=headers, max_retries=_DEFAULT_READ_MAX_RETRIES
    )


def get_order(
    *, order_id: str, live_mode: bool = False, live_keys_present: bool = False
) -> OrderResult:
    """Poll a single order's current broker-truth status. Never raises.

    AC-8: the broker is the source of truth for stop/bracket order state --
    calling this fresh (no shared in-memory object) is exactly how a
    restarted engine confirms a protective order is still live.
    """
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/orders/{order_id}"
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry("get", url, headers=headers, max_retries=_DEFAULT_READ_MAX_RETRIES)


def get_order_by_client_order_id(
    *, client_order_id: str, live_mode: bool = False, live_keys_present: bool = False
) -> OrderResult:
    """Look up a broker order by the client_order_id minted before submitting.

    Lost-ack recovery pattern: if a submit's HTTP response is lost (e.g. a
    connection reset after the broker already processed the request), the
    caller can recover the broker's actual order state via the SAME
    client_order_id it generated before the original call -- independent of
    whether our own process ever saw a response. Queries Alpaca's documented
    ``GET /v2/orders:by_client_order_id`` endpoint. Never raises.
    """
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/orders:by_client_order_id?client_order_id={quote(client_order_id, safe='')}"
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry("get", url, headers=headers, max_retries=_DEFAULT_READ_MAX_RETRIES)


def get_account(*, live_mode: bool = False, live_keys_present: bool = False) -> OrderResult:
    """Fetch broker-truth account state (cash, equity, buying power). Never raises."""
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/account"
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry("get", url, headers=headers, max_retries=_DEFAULT_READ_MAX_RETRIES)


def get_positions(*, live_mode: bool = False, live_keys_present: bool = False) -> OrderResult:
    """Fetch broker-truth open positions. Never raises.

    On success, ``order`` holds a list (not a dict) -- the raw Alpaca
    ``GET /v2/positions`` response shape.
    """
    host = resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
    url = f"{host}/v2/positions"
    headers = _credential_headers(live_mode=live_mode, live_keys_present=live_keys_present)
    return _request_with_retry("get", url, headers=headers, max_retries=_DEFAULT_READ_MAX_RETRIES)
