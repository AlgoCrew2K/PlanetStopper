"""sleeves/tick_orchestrator.py -- the P3 engine-tick entry point (AC-9, AC-10).

The single function a fresh-subprocess-per-minute engine tick calls
(``run_sleeve_tick_for_all_sleeves``). For every sleeve it, in order:

  0. Polls the broker for fills on already-acked, non-terminal orders
     (``poll_and_apply_fills``) and records any newly-reported fill. Runs for
     EVERY sleeve, SHADOW included (s3-review BLOCK 1): a disarmed sleeve
     stays SHADOW permanently until re-armed and can still hold real
     residual broker-side exposure (a TOCTOU fill between the disarm click
     and this tick's cancel attempt, or a pre-disarm position) -- "no
     live-armed rules" does not mean "nothing at the broker to track".
  1. SHADOW-status sleeves THEN get cancel_open_orders_for_shadow_sleeve
     (AC-12 disarm support -- the disarm route can never itself reach
     sleeves.alpaca_orders, so this tick is where a disarmed sleeve's
     lingering open orders actually get cancelled). Fill-poll runs STRICTLY
     BEFORE this cleanup (audit 2026-07-09 #4, live-observed): cancel-first
     marked a broker-FILLED order "canceled", permanently lost the fill,
     and released its reservation while the broker held the shares. The
     cleanup itself also re-polls after every accepted DELETE (INFO-002:
     Alpaca cancels the LEGS of a filled bracket parent and reports
     success) so a fill landing inside this tick's poll->cancel window is
     still recorded, never overwritten.
  2. AGGREGATE reconciliation across every non-already-paused sleeve
     (``_run_aggregate_reconciliation``, grouped by resolved broker host) --
     see "Shared-account reconciliation semantics" below. A cash breach
     pauses every sleeve in the group; a per-symbol position breach pauses
     only the sleeves holding that symbol. Either pause this tick (or a
     sleeve already PAUSED_RECONCILIATION coming in) skips rule evaluation
     for it this tick. SHADOW sleeves participate exactly like any other.
  3. Otherwise assembles the sleeve's sense context and dispatches through
     ``sleeves.rules.runner.evaluate_rules`` (the P2 rule engine) -- this
     still includes SHADOW-status sleeves (AC-6: a SHADOW rule
     senses/evaluates/records fires; only PAUSED_RECONCILIATION skips it).

A single sleeve's exception at any of these steps is caught and logged;
processing continues for the remaining sleeves -- mirrors the fail-safe
containment contract ``alpha_bot_execution.main()`` applies to this whole
module (a sleeve bug must never cost a symphony its exit, and here, one
sleeve's bug must never cost another sleeve its tick).

Broker-error observability (s3-review BLOCK 2): sleeves/alpaca_orders.py
never logs internally by design (its own never-raises contract) -- a broker
error surfaced to ``poll_and_apply_fills`` or
``cancel_open_orders_for_shadow_sleeve`` is logged at WARNING here, the only
place in the stack that can. A cancel failure on the disarm path additionally
posts a best-effort Discord alert: an operator who clicked disarm must never
be left believing an order is cancelled when the broker actually rejected
the request.

Shared-account reconciliation semantics (BLOCK: s3-ux live finding + PM
ruling, 2026-07-08): RETIRED the earlier per-sleeve reconcile_sleeve_or_pause,
which compared ONE sleeve's ledger cash against sleeves.alpaca_orders.
get_account's WHOLE-ACCOUNT cash figure. The broker has no concept of our
virtual per-sleeve partitioning -- a live 7-sleeve render reproduced exactly
the resulting bug (every sleeve paused within one tick, since each sleeve's
own necessarily-smaller capital differed from the full account total by far
more than any tolerance). The replacement, ``_run_aggregate_reconciliation``,
computes ONE aggregate check per tick (get_account/get_positions called
exactly once per broker-host group, never per sleeve):
  - CASH is one-sided: Sigma(every checked sleeve's ledger cash_usd +
    reserved_usd) <= account_cash + cash_tolerance_usd. Account cash
    EXCEEDING the sleeve sum is fine by design (unallocated float, or the
    operator's own money sharing the account) -- only the sleeves'
    collective claim exceeding the account is a breach, and it pauses EVERY
    checked sleeve (blame is unattributable across virtual slices of one
    real account).
  - POSITIONS stay per-symbol but are aggregated across sleeves: for every
    symbol any checked sleeve currently holds a nonzero qty in, Sigma(every
    such sleeve's qty) <= the broker's own qty for that symbol + tolerance.
    A breach pauses only the sleeves holding that symbol. A broker position
    in a symbol NO sleeve has ever touched (an operator-external holding
    sharing the account) is ignored entirely. A broker position in a symbol
    some sleeve has ORDER HISTORY in while no ledger currently holds it is
    an unexplained NAKED position (audit 2026-07-09 #4 residue) and pauses
    the sleeves with history in that symbol -- see the inline note in
    _run_aggregate_reconciliation for the accepted false-positive class.
  - Per-sleeve cash conservation remains sleeves.ledger's own internal law
    (already enforced there); it is simply no longer checked against the
    broker on a per-sleeve basis.
See sleeves/reconciliation.py's reconcile_aggregate_cash/
reconcile_aggregate_position for the underlying one-sided pure-function
contract.

Daily-bar/FRED sense wiring (epic-done-bar fix, task #33, 2026-07-08): the
PM's real paper-account smoke found ``closes_by_symbol``/``fred_cache`` were
hardcoded ``{}`` literals, so ``price = closes[-1] if closes else 0.0``
always yielded ``0.0`` and every entry action refused with
``error="invalid_price"`` -- an armed sleeve could never genuinely trade.
Fixed: ``run_sleeve_tick_for_all_sleeves`` now collects every symbol
referenced by an ENABLED rule across every evaluable sleeve, fetches daily
closes for that whole set via ``synthetic_history.fetch_bars`` EXACTLY ONCE
per tick (the same daily-bar path P1/P2 already use for history -- never a
second HTTP client), and reads cached FRED observations from
``database.get_latest_market_lens_cache()`` (cache-only, D-1 -- no live FRED
call from the tick, matching ``ai_advisor.py``'s own cache-serve path). A
symbol with no bars available (omitted from the fetch response, or the
fetch itself failing) still fails safe through the existing
``sleeves.rules.senses``/``conditions`` empty-closes contract (unavailable
sense -> not fireable, no fire recorded) -- never a crash, never a
fabricated price.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import database
import sleeves.alpaca_orders as alpaca_orders
import sleeves.ledger as ledger
import sleeves.reconciliation as reconciliation
import sleeves.rules.runner as runner
import synthetic_history
from sleeves.rules.limits import STALE_NO_BARS_KEY

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Reuses database.get_daily_turnover_usd's exact terminal-status denylist so
# "has this order reached a status where further polling is pointless" stays
# consistent with the rest of the codebase's Alpaca-status classification.
_TERMINAL_ORDER_STATUSES = (
    "filled",
    "canceled",
    "expired",
    "replaced",
    "done_for_day",
    "rejected",
)

# Matches sleeves/rules/actions.py's _NOTIFY_REQUEST_TIMEOUT_S -- same
# never-hang-the-tick-on-Discord convention, same constant value.
_ALERT_REQUEST_TIMEOUT_S = 10.0

# Reconciliation tolerances (AC-9). Values match the fixture tolerances this
# module's own RED test suite (tests/sleeves/test_tick_orchestrator.py)
# exercises; not yet operator-configurable per sleeve -- a tracked follow-up
# for the arming/CRUD routes to expose these on the sleeve record itself.
_DEFAULT_POSITION_TOLERANCE_PCT = 0.005
_DEFAULT_CASH_TOLERANCE_USD = 1.0

# Daily-bar lookback window passed to synthetic_history.fetch_bars. 270
# calendar days covers ~250 trading days (weekends/holidays) -- matches
# advisors/lens_technicals.py's own _HISTORY_DAYS window exactly, so the
# longest sleeve-rule indicator (e.g. a 200-day SMA) has the same history
# depth already proven sufficient for that lens.
_BARS_HISTORY_DAYS = 270


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_discord_alert(discord_webhook_url: str | None, message: str) -> None:
    """Best-effort Discord post -- never raises (a delivery failure must
    never break the tick), mirrors sleeves/rules/actions.py's own notify
    dispatch convention exactly."""
    if not discord_webhook_url:
        return
    with contextlib.suppress(requests.RequestException):
        requests.post(
            discord_webhook_url,
            json={"content": message},
            timeout=_ALERT_REQUEST_TIMEOUT_S,
        )


def _compute_live_gates(sleeve_row: dict) -> tuple[bool, bool]:
    """Compute resolve_host's two caller-supplied booleans for one sleeve.

    sleeves/alpaca_orders.py's resolve_host() reads no environment itself
    (P1 invariant 3) -- computing live_mode (sleeve status + the
    SLEEVE_LIVE_EXECUTION master switch) and live_keys_present (live-key env
    presence) is documented as the P2/P3 runner's responsibility. Two
    independent gates for live_mode mirrors AC-14's "distinct live-key env
    vars AND a dedicated flag" posture: a sleeve armed LIVE with the flag
    unset (or vice versa) still resolves to the paper host.
    """
    live_mode = sleeve_row.get("status") == "LIVE" and os.getenv(
        "SLEEVE_LIVE_EXECUTION", "False"
    ).lower() in ("true", "1", "yes")
    live_keys_present = bool(os.getenv("ALPACA_LIVE_KEY")) and bool(os.getenv("ALPACA_LIVE_SECRET"))
    return live_mode, live_keys_present


# ---------------------------------------------------------------------------
# poll_and_apply_fills
# ---------------------------------------------------------------------------


# Sentinel returned by _record_new_fill_delta when the broker reports new
# filled quantity WITHOUT a usable price (propagation lag): the caller must
# NOT advance the order's status -- especially not to a terminal one, since
# terminal rows are never re-polled and the fill would be lost permanently.
_FILL_DELTA_DEFERRED = object()


def _record_new_fill_delta(order: dict, broker_order: dict) -> dict | object | None:
    """Record any broker-reported fill quantity beyond what ``sleeve_fills``
    already holds for this order. Returns the newly-recorded fill dict; None
    when the broker reports nothing new; or ``_FILL_DELTA_DEFERRED`` when a
    new quantity exists but carries no usable price yet -- the caller must
    leave the row's status untouched (non-terminal) and retry next tick.

    The delta math (broker cumulative filled_qty minus already-recorded qty)
    is the load-bearing idempotency mechanism; the deterministic
    ``broker_fill_id`` -- ``{alpaca_order_id}:{cumulative qty}`` -- is
    schema-UNIQUE defense-in-depth on top. Shared by the fill poll AND the
    cancel path (audit 2026-07-09 #4/INFO-002: Alpaca accepts DELETE on a
    FILLED bracket parent, so a cancel must reconcile fill truth before any
    terminal status write).
    """
    recorded_fills = database.get_fills_for_order(order["id"])
    already_recorded_qty = sum(f["filled_qty"] for f in recorded_fills)
    broker_filled_qty = float(broker_order.get("filled_qty") or 0.0)
    delta_qty = broker_filled_qty - already_recorded_qty
    if delta_qty <= 0:
        return None

    filled_at = broker_order.get("filled_at") or broker_order.get("updated_at") or _utcnow_iso()
    # Audit 2026-07-09 #6: Alpaca's filled_avg_price is the CUMULATIVE
    # average across every fill of the order, not this delta's own price --
    # booking the delta at the average drifts cost basis/cash from broker
    # truth by q1*q2*(p1-p2)/(q1+q2) on every multi-price partial fill. The
    # delta's own implied price falls out of the two cumulative notionals:
    # (broker cumulative notional - already-recorded notional) / delta qty.
    broker_avg_price = float(broker_order.get("filled_avg_price") or 0.0)
    already_recorded_notional = sum(f["filled_qty"] * f["fill_price"] for f in recorded_fills)
    delta_notional = broker_filled_qty * broker_avg_price - already_recorded_notional
    if delta_notional <= 0.0:
        # Broker reports new filled qty but no coherent price for it (null
        # filled_avg_price during propagation lag, or a cumulative notional
        # below what is already recorded). Never book a fill at a zero/
        # negative price -- the ledger rejects price <= 0 and a poisoned row
        # would brick every subsequent reconstruction for this sleeve.
        logger.warning(
            "_record_new_fill_delta: deferring fill delta for order %s "
            "(qty delta %s) -- non-positive delta notional %s from broker avg %s; "
            "status left untouched, retry next poll",
            order["alpaca_order_id"],
            delta_qty,
            delta_notional,
            broker_avg_price,
        )
        return _FILL_DELTA_DEFERRED
    fill_price = delta_notional / delta_qty
    broker_fill_id = f"{order['alpaca_order_id']}:{broker_filled_qty}"
    database.insert_sleeve_fill(
        order_id=order["id"],
        fill_price=fill_price,
        filled_qty=delta_qty,
        filled_at=filled_at,
        broker_fill_id=broker_fill_id,
    )
    return {
        "order_id": order["id"],
        "symbol": order["symbol"],
        "filled_qty": delta_qty,
        "fill_price": fill_price,
        "filled_at": filled_at,
    }


def poll_and_apply_fills(
    sleeve_id: int, *, live_mode: bool = False, live_keys_present: bool = False
) -> list[dict]:
    """Poll broker-truth status for every non-terminal sleeve order.

    A non-terminal row with no ``alpaca_order_id`` (a submit whose ack was
    lost, or a crash between reserve and submit) goes through lost-ack
    recovery via its minted client_order_id (audit 2026-07-09 #10): found at
    the broker -> the broker order is adopted (id attached, fills/status
    below apply); HTTP 404 -> the submit definitively never landed, the row
    is marked "rejected" (releasing the reservation); any other error ->
    retried next tick with the reservation still held. When the broker
    reports a filled quantity beyond what is already recorded in
    ``sleeve_fills`` for an order, the delta is inserted as a new fill row
    and the order's status advances to the broker's own status string
    verbatim (never an invented synonym). Returns the list of
    newly-inserted fill dicts (empty if nothing new).
    """
    new_fills: list[dict] = []
    for order in database.get_sleeve_orders(sleeve_id=sleeve_id, limit=500):
        if order["status"] in _TERMINAL_ORDER_STATUSES:
            continue  # already terminal -- nothing further to poll

        if not order.get("alpaca_order_id"):
            # Non-terminal row with no broker order id: a submit whose ack
            # was lost (timeout -- "the server may still be processing"), or
            # a crash between the RESERVED insert and the broker call. Both
            # hold a cash reservation that only broker truth can resolve --
            # lost-ack recovery via the client_order_id minted before the
            # submit (audit 2026-07-09 #10: this helper existed with zero
            # production callers while timeouts released reservations on
            # possibly-live orders).
            recovery = alpaca_orders.get_order_by_client_order_id(
                client_order_id=order["client_order_id"],
                live_mode=live_mode,
                live_keys_present=live_keys_present,
            )
            if recovery.error is None and recovery.order:
                broker_order_id = recovery.order.get("id")
                database.attach_alpaca_order_id(
                    client_order_id=order["client_order_id"], alpaca_order_id=broker_order_id
                )
                order = {**order, "alpaca_order_id": broker_order_id}
                # Fall through to the normal poll flow below with the
                # recovered broker state -- fills recorded, status verbatim.
                result = alpaca_orders.OrderResult(order=recovery.order, error=None)
            elif recovery.error == "HTTP 404":
                # The broker has no order under our minted client_order_id:
                # the submit definitively never landed. Terminal "rejected"
                # releases the reservation on the next reconstruction.
                database.update_sleeve_order_status(order["client_order_id"], "rejected")
                continue
            else:
                logger.warning(
                    "poll_and_apply_fills: lost-ack recovery failed for "
                    "client_order_id %s (sleeve %s): %s -- reservation held, "
                    "retry next tick",
                    order["client_order_id"],
                    sleeve_id,
                    recovery.error,
                )
                continue
        else:
            result = alpaca_orders.get_order(
                order_id=order["alpaca_order_id"],
                live_mode=live_mode,
                live_keys_present=live_keys_present,
            )
        if result.error is not None or result.order is None:
            # BLOCK 2 (s3-review): alpaca_orders never logs internally --
            # this is the only place a persistent outage/auth failure on the
            # fill-polling path becomes observable.
            logger.warning(
                "poll_and_apply_fills: broker error polling order %s (sleeve %s): %s",
                order["alpaca_order_id"],
                sleeve_id,
                result.error,
            )
            continue  # broker unreachable this tick -- retry next tick

        broker_order = result.order
        delta_outcome = _record_new_fill_delta(order, broker_order)
        if delta_outcome is _FILL_DELTA_DEFERRED:
            # A broker-reported fill exists but could not be honestly priced
            # yet -- leave the status untouched (advancing to a terminal
            # status here would lose the fill permanently) and retry next
            # tick.
            continue
        if delta_outcome is not None:
            new_fills.append(delta_outcome)

        broker_status = broker_order.get("status")
        if broker_status:
            database.update_sleeve_order_status(
                order["client_order_id"], broker_status, raw_json=json.dumps(broker_order)
            )

    if new_fills:
        # Audit 2026-07-09 #13: AC-1's cash floor is enforced at reserve time
        # (qty x tick price), but entries are MARKET orders -- an unfavorable
        # fill books the shortfall floor-lessly and sleeve cash goes negative
        # with no signal anywhere (the one-sided aggregate cash check only
        # sees OVER-claims; a negative claim is an under-claim). The recorder
        # is the one place that knows a new fill just landed, so it owns the
        # operator signal.
        sleeve_row = database.get_sleeve(sleeve_id)
        if sleeve_row:
            state = ledger.reconstruct_from_history(
                sleeve_row["capital_usd"], database.get_sleeve_order_history(sleeve_id)
            )
            if state.cash_usd < 0:
                logger.warning(
                    "sleeve %s cash is NEGATIVE (%.2f USD) after recording fills -- "
                    "AC-1's allocation floor was breached by market slippage between "
                    "the reserve-time price and the actual fill price",
                    sleeve_id,
                    state.cash_usd,
                )

    return new_fills


# ---------------------------------------------------------------------------
# cancel_open_orders_for_shadow_sleeve
# ---------------------------------------------------------------------------


def cancel_open_orders_for_shadow_sleeve(
    sleeve_id: int,
    *,
    live_mode: bool = False,
    live_keys_present: bool = False,
    discord_webhook_url: str | None = None,
) -> list[dict]:
    """AC-12 disarm support: cancel every non-terminal broker order for a
    SHADOW-status sleeve.

    The POST /api/sleeves/<id>/disarm ROUTE never itself reaches
    sleeves.alpaca_orders (that would trip the whole-app.py order-path
    containment scan, AC-15) -- it only reverts the sleeve/rules to SHADOW
    synchronously. THIS function is where the actual broker cancellation
    happens (called by run_sleeve_tick_for_all_sleeves for every
    SHADOW-status sleeve): it cancels every non-terminal sleeve_orders row
    and never touches positions or broker-side stops (no close_position/
    liquidate_position call) -- disarm is non-destructive by design. A
    sleeve with zero non-terminal orders is a no-op. Returns the list of
    sleeve_orders rows that were cancelled.

    A broker error on cancel_order (s3-review BLOCK 2) is logged at WARNING
    and best-effort posts a Discord alert -- this path is safety-critical: an
    operator who clicked disarm must never be left believing an order is
    cancelled when the broker actually rejected the cancel request.

    Fill-safety on the cancel path (audit 2026-07-09 #4, live-observed money
    loss; INFO-002): Alpaca accepts DELETE on a FILLED bracket parent (it
    cancels the LEGS), so a successful cancel must NEVER be read as "the
    order didn't fill". After every accepted DELETE this function re-polls
    broker truth, records any fill delta, and only then writes a terminal
    status: the broker's own status verbatim when the broker is already
    terminal (a FILLED order stays "filled" -- its reservation resolves into
    the position instead of releasing), or "canceled" when the broker is
    still non-terminal (the cancel is merely propagating and no fill exists
    as of this poll). If the post-cancel re-poll itself fails, the row is
    left NON-terminal so the next tick's poll can still reconcile a possible
    fill -- a blind "canceled" write here is exactly how the audit's live
    smoke lost a real fill.
    """
    cancelled: list[dict] = []
    for order in database.get_sleeve_orders(sleeve_id=sleeve_id, limit=500):
        if not order.get("alpaca_order_id"):
            continue  # pre-ack -- nothing at the broker to cancel yet
        if order["status"] in _TERMINAL_ORDER_STATUSES:
            continue  # already terminal -- nothing to cancel

        result = alpaca_orders.cancel_order(
            order_id=order["alpaca_order_id"],
            live_mode=live_mode,
            live_keys_present=live_keys_present,
        )
        if result.error is not None:
            logger.warning(
                "cancel_open_orders_for_shadow_sleeve: broker error cancelling "
                "order %s (sleeve %s): %s",
                order["alpaca_order_id"],
                sleeve_id,
                result.error,
            )
            _post_discord_alert(
                discord_webhook_url,
                f"Sleeve {sleeve_id} disarm: failed to cancel order "
                f"{order['alpaca_order_id']} ({order.get('symbol')}) -- broker "
                f"error: {result.error}. The order may still be live at the "
                f"broker despite the disarm.",
            )
            continue  # broker unreachable this tick -- retry next tick

        poll_result = alpaca_orders.get_order(
            order_id=order["alpaca_order_id"],
            live_mode=live_mode,
            live_keys_present=live_keys_present,
        )
        if poll_result.error is not None or poll_result.order is None:
            # Fail closed: the DELETE was accepted but broker truth is
            # unknowable right now -- leave the row non-terminal so the next
            # tick's poll reconciles any fill before a terminal status lands.
            logger.warning(
                "cancel_open_orders_for_shadow_sleeve: cancel accepted but "
                "post-cancel poll failed for order %s (sleeve %s): %s -- "
                "leaving status non-terminal for next tick's reconciliation",
                order["alpaca_order_id"],
                sleeve_id,
                poll_result.error,
            )
            continue

        broker_order = poll_result.order
        if _record_new_fill_delta(order, broker_order) is _FILL_DELTA_DEFERRED:
            # An unrecordable (price-less) fill exists on this order: writing
            # ANY terminal status now would strand it forever. Leave the row
            # non-terminal; next tick's poll re-reconciles before this
            # cleanup sees the order again.
            continue
        broker_status = broker_order.get("status")
        if broker_status in _TERMINAL_ORDER_STATUSES:
            # Broker already reached its own verdict (e.g. "filled" before
            # our DELETE landed) -- record it verbatim, never overwrite a
            # fill with "canceled".
            database.update_sleeve_order_status(
                order["client_order_id"], broker_status, raw_json=json.dumps(broker_order)
            )
            if broker_status == "canceled":
                cancelled.append(order)
            continue

        # Broker still non-terminal: the accepted DELETE is propagating and
        # no unrecorded fill exists as of the poll above -- "canceled" is the
        # correct terminal verdict for our books.
        database.update_sleeve_order_status(order["client_order_id"], "canceled")
        cancelled.append(order)

    return cancelled


# ---------------------------------------------------------------------------
# Aggregate shared-account reconciliation (BLOCK: s3-ux live finding + PM
# ruling, 2026-07-08) -- see the module docstring's "Shared-account
# reconciliation semantics" section for the full rationale.
# ---------------------------------------------------------------------------


def _pause_sleeve_for_aggregate_breach(
    sleeve_id: int, sleeve_name: str, reason: str, discord_webhook_url: str | None
) -> None:
    database.update_sleeve_status(sleeve_id, "PAUSED_RECONCILIATION")
    _post_discord_alert(
        discord_webhook_url,
        f"Sleeve {sleeve_id} ({sleeve_name}) paused: aggregate reconciliation breach -- {reason}",
    )


def _run_aggregate_reconciliation(
    sleeve_rows: list[dict],
    *,
    live_mode: bool,
    live_keys_present: bool,
    position_tolerance_pct: float,
    cash_tolerance_usd: float,
    discord_webhook_url: str | None,
) -> set[int]:
    """Aggregate, shared-account-aware reconciliation for one broker-host
    group. Fetches broker account/positions EXACTLY ONCE for the whole
    group (never per sleeve -- that per-sleeve fetch was the original bug).
    Returns the set of sleeve_ids paused during this call.
    """
    ledger_states: dict[int, ledger.LedgerState] = {}
    # symbol -> sleeve_ids with ANY recorded order history in it -- the
    # reconciliation scope extension of audit #4 (review gap G1): history,
    # not just current ledger qty, defines which symbols are "ours".
    history_symbols: dict[str, set[int]] = {}
    for sleeve_row in sleeve_rows:
        order_history = database.get_sleeve_order_history(sleeve_row["id"])
        ledger_states[sleeve_row["id"]] = ledger.reconstruct_from_history(
            sleeve_row["capital_usd"], order_history
        )
        for order in order_history:
            history_symbols.setdefault(order["symbol"], set()).add(sleeve_row["id"])

    positions_result = alpaca_orders.get_positions(
        live_mode=live_mode, live_keys_present=live_keys_present
    )
    account_result = alpaca_orders.get_account(
        live_mode=live_mode, live_keys_present=live_keys_present
    )

    if positions_result.error is not None or account_result.error is not None:
        # Fail-closed, mirrors the retired per-sleeve behavior: a broker API
        # failure must never be read as "the broker confirms zero drift" --
        # pause every sleeve in this group rather than mask a real mismatch
        # behind a transient outage.
        broker_error = positions_result.error or account_result.error
        paused_ids: set[int] = set()
        for sleeve_row in sleeve_rows:
            _pause_sleeve_for_aggregate_breach(
                sleeve_row["id"],
                sleeve_row["name"],
                f"broker_unreachable:{broker_error}",
                discord_webhook_url,
            )
            paused_ids.add(sleeve_row["id"])
        return paused_ids

    broker_cash_usd = float(account_result.order["cash"])
    broker_positions = {p["symbol"]: float(p["qty"]) for p in (positions_result.order or [])}

    total_cash_claim = sum(ls.cash_usd + ls.reserved_usd for ls in ledger_states.values())
    cash_result = reconciliation.reconcile_aggregate_cash(
        total_sleeve_cash_claim_usd=total_cash_claim,
        broker_cash_usd=broker_cash_usd,
        cash_tolerance_usd=cash_tolerance_usd,
    )
    if not cash_result.ok:
        # An aggregate cash breach pauses EVERY sleeve in the group -- blame
        # is unattributable across virtual slices of one real account.
        paused_ids = set()
        for sleeve_row in sleeve_rows:
            _pause_sleeve_for_aggregate_breach(
                sleeve_row["id"], sleeve_row["name"], cash_result.breaches[0], discord_webhook_url
            )
            paused_ids.add(sleeve_row["id"])
        return paused_ids

    # Per-symbol position aggregation: symbol -> {sleeve_id: qty} for every
    # sleeve currently holding a nonzero qty in that symbol. A symbol no
    # sleeve holds never enters this map, so a broker-only ("operator
    # external") position is ignored entirely -- never even inspected.
    symbol_holders: dict[str, dict[int, float]] = {}
    for sleeve_id, ls in ledger_states.items():
        for symbol, pos in ls.positions.items():
            if pos.qty:
                symbol_holders.setdefault(symbol, {})[sleeve_id] = pos.qty

    sleeve_names = {row["id"]: row["name"] for row in sleeve_rows}
    paused_ids = set()
    for symbol, holders in symbol_holders.items():
        total_qty = sum(holders.values())
        broker_qty = broker_positions.get(symbol, 0.0)
        position_result = reconciliation.reconcile_aggregate_position(
            symbol=symbol,
            total_sleeve_qty=total_qty,
            broker_qty=broker_qty,
            position_tolerance_pct=position_tolerance_pct,
        )
        if not position_result.ok:
            for sleeve_id in holders:
                if sleeve_id in paused_ids:
                    continue
                _pause_sleeve_for_aggregate_breach(
                    sleeve_id,
                    sleeve_names[sleeve_id],
                    position_result.breaches[0],
                    discord_webhook_url,
                )
                paused_ids.add(sleeve_id)

    # Naked-position detection (audit #4's reconciliation-scope half, review
    # gap G1): a broker position in a symbol some checked sleeve has ORDER
    # history in, while NO checked sleeve's ledger currently holds it, is
    # unexplained drift -- the audit's live residue was exactly this state
    # (order marked canceled, zero fills recorded, ledger full-cash/flat,
    # broker holding shares bought with sleeve cash), structurally invisible
    # while operator float absorbed the one-sided cash check. Pause the
    # sleeves with history in that symbol. The operator-external exemption
    # survives untouched for symbols NO sleeve ever traded. Accepted
    # false-positive class (ratified with this pin): an operator's own
    # holding in a symbol a now-flat sleeve once traded will pause that
    # sleeve -- distinguishing the two is impossible from one shared
    # account, and pausing is the fail-closed direction.
    for symbol, history_sleeve_ids in history_symbols.items():
        if symbol in symbol_holders:
            continue  # a ledger holds it -- the one-sided check above owns it
        if not broker_positions.get(symbol):
            continue  # broker flat too -- a legitimately closed position
        for sleeve_id in history_sleeve_ids:
            if sleeve_id in paused_ids:
                continue
            _pause_sleeve_for_aggregate_breach(
                sleeve_id,
                sleeve_names[sleeve_id],
                f"naked_position:{symbol}",
                discord_webhook_url,
            )
            paused_ids.add(sleeve_id)

    return paused_ids


# ---------------------------------------------------------------------------
# run_sleeve_tick_for_all_sleeves
# ---------------------------------------------------------------------------


def _load_enabled_rules(sleeve_id: int) -> list[dict]:
    """DB rows -> the rule-dict shape sleeves.rules.runner.evaluate_rules
    expects: each row's parsed json_doc fields merged with the ROW's own
    id/sleeve_id/mode -- row columns are AUTHORITATIVE (audit 2026-07-09
    CRIT #1/#2). The create route stores the operator's raw payload as
    json_doc; sleeve_id lives only in the URL and the sleeve_rules.sleeve_id
    column, and the row's mode is forced SHADOW at creation and changed only
    by the arm ceremony. A doc-supplied copy of id/sleeve_id/mode is either
    an import artifact or an attack payload and must be inert: merging the
    doc FIRST means the row values always win."""
    rules: list[dict] = []
    for row in database.get_sleeve_rules_for_sleeve(sleeve_id):
        if not row.get("enabled"):
            continue
        try:
            doc = json.loads(row["json_doc"])
        except (TypeError, ValueError):
            logger.exception(
                "sleeve rule %s (sleeve %s) has invalid json_doc -- skipping this tick",
                row.get("id"),
                sleeve_id,
            )
            continue
        rules.append({**doc, "id": row["id"], "sleeve_id": row["sleeve_id"], "mode": row["mode"]})
    return rules


def _fetch_closes_for_symbols(symbols: set[str], *, now_et: datetime) -> dict[str, list[float]]:
    """Fetch daily closes for every referenced symbol, ONCE per tick.

    Delegates to synthetic_history.fetch_bars -- the SAME daily-bar path
    P1/P2 already use for history, never a second HTTP client. Never raises:
    a network failure or a symbol the response omitted both degrade to an
    empty closes list for that symbol, which sleeves.rules.senses/conditions
    already treats as an unavailable sense (not fireable, no fire recorded)
    -- the existing fail-safe contract, not a new one.
    """
    if not symbols:
        return {}
    end_dt = now_et.date()
    start_dt = end_dt - timedelta(days=_BARS_HISTORY_DAYS)
    try:
        bars_by_symbol = synthetic_history.fetch_bars(
            sorted(symbols), start_dt.isoformat(), end_dt.isoformat(), timeframe="1Day"
        )
    except Exception:
        logger.exception(
            "bar fetch failed for this tick's symbol set %s -- all referenced "
            "symbols fail safe (no closes) rather than crash the tick",
            sorted(symbols),
        )
        return {}
    return {
        symbol: [bar["c"] for bar in bar_list]
        for symbol, bar_list in (bars_by_symbol or {}).items()
        if bar_list
    }


def _build_fred_cache() -> dict[str, list[dict]]:
    """Cache-only FRED bundle for the tick's sense context (D-1: no live FRED
    call from the engine tick -- matches ai_advisor.py's own cache-serve
    precedent, database.get_latest_market_lens_cache()).

    Reads the nightly MARKET_LENS_CACHE bundle's "macro" lens block
    (ai_advisor.py's _build_macro_section, ai_advisor.py:768:
    raw_response["lenses"]["macro"]["payload"]["series"], shaped
    {series_id: {"label", "value", "date"}} -- ONE latest observation per
    series) and wraps each series' single observation into the
    one-element-list shape sleeves.rules.senses.sense_fred_series expects
    ({series_id: [{"date": ..., "value": ...}]}).

    FRED's own API returns the literal string "." for a missing/gap
    observation, never null and never an omitted key (s3-review follow-up,
    2026-07-08) -- a bare "." threaded into fred_cache would reach
    conditions.py's numeric comparator as a string, raising TypeError
    instead of degrading through the intended "unavailable sense -> not
    fireable" fail-safe path. Coercing through float() here excludes that
    sentinel (and any other non-numeric value) the same way a missing
    observation already would.
    """
    cached_row = database.get_latest_market_lens_cache()
    if not cached_row:
        return {}
    raw_response = cached_row.get("raw_response") or {}
    macro_lens = (raw_response.get("lenses") or {}).get("macro") or {}
    series_data = (macro_lens.get("payload") or {}).get("series") or {}

    fred_cache: dict[str, list[dict]] = {}
    for series_id, obs in series_data.items():
        if not isinstance(obs, dict) or obs.get("date") is None:
            continue
        try:
            numeric_value = float(obs["value"])
        except (KeyError, TypeError, ValueError):
            continue  # FRED "." sentinel (or any other non-numeric value)
        fred_cache[series_id] = [{"date": obs["date"], "value": numeric_value}]
    return fred_cache


def _book_equity_usd(
    ledger_state: ledger.LedgerState, closes_by_symbol: dict[str, list[float]]
) -> float:
    """Sleeve equity for risk sizing: cash + reservations + open positions
    MARKED TO THE TICK'S OWN FETCHED CLOSES (audit 2026-07-09 #12).

    The earlier cost-basis-only figure claimed to be "never conservatively
    high" -- false in exactly the dangerous direction: with unrealized
    LOSSES, cost basis EXCEEDS market value, so risk_pct sizing and the
    max_position_pct cap both overshoot true equity precisely when the
    sleeve is drawn down. Marking to the closes already fetched once per
    tick fixes that with zero additional broker round-trips. A position
    whose symbol has no closes THIS tick falls back to its cost basis --
    the only figure available without a broker call; the fallback is
    stale-priced, not directionally safe, which is why the daily-bar fetch
    covers every enabled rule's symbol in the first place.
    """
    equity_usd = ledger_state.cash_usd + ledger_state.reserved_usd
    for symbol, position in ledger_state.positions.items():
        closes = closes_by_symbol.get(symbol)
        if closes:
            equity_usd += position.qty * closes[-1]
        else:
            equity_usd += position.cost_basis_usd
    return equity_usd


def run_sleeve_tick_for_all_sleeves(
    *, now_utc: datetime, discord_webhook_url: str | None = None
) -> list:
    """The single entry point one engine tick calls for every managed sleeve.

    Per sleeve:
      0. poll_and_apply_fills -- runs for every sleeve not already
         PAUSED_RECONCILIATION coming into this tick, STRICTLY BEFORE the
         SHADOW cleanup below (audit 2026-07-09 #4: cancel-before-poll
         marked a broker-FILLED order "canceled" and lost the fill).
      1. SHADOW-status sleeves THEN get cancel_open_orders_for_shadow_sleeve
         (AC-12 design correction -- the disarm ROUTE can never itself reach
         sleeves.alpaca_orders, so this tick is where that cancellation
         actually happens). This is an ADDITIONAL step, not a replacement
         for step 0 above (s3-review BLOCK 1): a disarmed sleeve stays
         SHADOW permanently until re-armed and can still hold real
         broker-side exposure, so it is not exempt from fill-polling or
         reconciliation. A PAPER/LIVE-status sleeve is NEVER cleanup-eligible
         -- armed sleeves keep their resting orders (audit #3).
      2. AGGREGATE reconciliation, once per tick, across every sleeve that
         is neither already-paused-coming-in nor failed step 1 above
         (grouped by resolved broker host -- see the module docstring's
         "Shared-account reconciliation semantics" section). A cash breach
         pauses every sleeve in the group; a per-symbol position breach
         pauses only the sleeves holding that symbol. Either pause skips
         rule evaluation for the affected sleeve(s) this tick.
      3. Otherwise assembles the sleeve's sense context and dispatches
         through sleeves.rules.runner.evaluate_rules -- this still includes
         SHADOW-status sleeves (AC-6: a SHADOW rule senses/evaluates/records
         fires; only PAUSED_RECONCILIATION skips rule evaluation).

    A single sleeve's exception anywhere in this sequence is caught and
    logged; processing continues for the remaining sleeves.
    """
    outcomes: list = []
    all_sleeve_rows = database.get_all_sleeves()

    # --- Steps 0-1: per-sleeve SHADOW cleanup + fill-polling. -----------
    already_paused_ids: set[int] = set()
    phase1_failed_ids: set[int] = set()

    for sleeve_row in all_sleeve_rows:
        sleeve_id = sleeve_row["id"]
        try:
            live_mode, live_keys_present = _compute_live_gates(sleeve_row)

            if sleeve_row["status"] == "PAUSED_RECONCILIATION":
                already_paused_ids.add(sleeve_id)
                continue

            # Fill-poll STRICTLY BEFORE the SHADOW cleanup (audit 2026-07-09
            # #4, live-observed): a fill that landed between ack and this
            # tick must be recorded (advancing the order to its terminal
            # broker status) before any cancel attempt can touch the order --
            # cancel-first marked a broker-FILLED order "canceled", lost the
            # fill forever, and released its reservation.
            poll_and_apply_fills(
                sleeve_id, live_mode=live_mode, live_keys_present=live_keys_present
            )

            if sleeve_row["status"] == "SHADOW":
                cancel_open_orders_for_shadow_sleeve(
                    sleeve_id,
                    live_mode=live_mode,
                    live_keys_present=live_keys_present,
                    discord_webhook_url=discord_webhook_url,
                )
        except Exception:
            logger.exception(
                "sleeve %s tick processing failed (fill-poll phase); other sleeves unaffected",
                sleeve_id,
            )
            phase1_failed_ids.add(sleeve_id)

    # --- Step 2: aggregate reconciliation, once per tick per broker host. ---
    skip_rule_eval_ids: set[int] = already_paused_ids | phase1_failed_ids
    checkable_rows = [row for row in all_sleeve_rows if row["id"] not in skip_rule_eval_ids]

    if checkable_rows:
        # Every checkable sleeve resolves to the SAME broker host today (no
        # LIVE-armed sleeve exists yet -- P3 scope), so this is one group in
        # practice; grouping by resolved host keeps the aggregate math
        # correct if/when a LIVE sleeve is ever provisioned (aggregating
        # cash/positions across two different broker accounts would be
        # meaningless).
        groups: dict[str, list[dict]] = {}
        for row in checkable_rows:
            live_mode, live_keys_present = _compute_live_gates(row)
            host = alpaca_orders.resolve_host(
                live_mode=live_mode, live_keys_present=live_keys_present
            )
            groups.setdefault(host, []).append(row)

        for host_rows in groups.values():
            group_live_mode, group_live_keys_present = _compute_live_gates(host_rows[0])
            try:
                skip_rule_eval_ids |= _run_aggregate_reconciliation(
                    host_rows,
                    live_mode=group_live_mode,
                    live_keys_present=group_live_keys_present,
                    position_tolerance_pct=_DEFAULT_POSITION_TOLERANCE_PCT,
                    cash_tolerance_usd=_DEFAULT_CASH_TOLERANCE_USD,
                    discord_webhook_url=discord_webhook_url,
                )
            except Exception:
                logger.exception(
                    "aggregate reconciliation failed this tick for a sleeve "
                    "group; skipping rule evaluation for it this tick"
                )
                skip_rule_eval_ids |= {row["id"] for row in host_rows}

    # --- Step 3: rule evaluation, using bars/FRED fetched ONCE for the whole
    # tick (epic-done-bar fix, task #33) -- never per sleeve.
    now_et = now_utc.astimezone(_ET)
    evaluable_rows = [row for row in all_sleeve_rows if row["id"] not in skip_rule_eval_ids]

    rules_by_sleeve: dict[int, list[dict]] = {}
    referenced_symbols: set[str] = set()
    for sleeve_row in evaluable_rows:
        sleeve_id = sleeve_row["id"]
        try:
            rules = _load_enabled_rules(sleeve_id)
        except Exception:
            logger.exception(
                "sleeve %s tick processing failed (rule-loading phase); other sleeves unaffected",
                sleeve_id,
            )
            continue
        rules_by_sleeve[sleeve_id] = rules
        for rule in rules:
            when = rule.get("when")
            if isinstance(when, dict) and "symbol" in when:
                referenced_symbols.add(when["symbol"])

    try:
        closes_by_symbol = _fetch_closes_for_symbols(referenced_symbols, now_et=now_et)
    except Exception:
        logger.exception(
            "bar-fetch phase failed unexpectedly this tick; all referenced "
            "symbols fail safe (no closes)"
        )
        closes_by_symbol = {}

    # Stale-rule visibility (feature-plan edge case "delisted/renamed symbol
    # -> rule flagged stale"; AC-16's 'stale' badge): a rule whose symbol
    # produced no bars while OTHER symbols did is durably flagged in
    # sleeve_runtime so the panel can tell a dead rule from a quiet one; a
    # tick where the WHOLE feed returned nothing flags nobody (a feed outage
    # is not per-symbol staleness), and a symbol that recovers clears its
    # flag the same way.
    if closes_by_symbol:
        for rules in rules_by_sleeve.values():
            for rule in rules:
                when = rule.get("when")
                if not (isinstance(when, dict) and "symbol" in when):
                    continue
                is_stale = when["symbol"] not in closes_by_symbol
                try:
                    database.set_sleeve_runtime(
                        rule["id"], STALE_NO_BARS_KEY, "1" if is_stale else "0"
                    )
                except Exception:
                    logger.exception(
                        "failed to persist stale flag for rule %s; flag stays "
                        "as-was, evaluation unaffected",
                        rule.get("id"),
                    )

    if any(rules_by_sleeve.values()):
        try:
            fred_cache = _build_fred_cache()
        except Exception:
            logger.exception("FRED-cache read failed unexpectedly this tick; FRED senses fail safe")
            fred_cache = {}
    else:
        fred_cache = {}

    for sleeve_row in evaluable_rows:
        sleeve_id = sleeve_row["id"]
        rules = rules_by_sleeve.get(sleeve_id)
        if not rules:
            continue
        try:
            order_history = database.get_sleeve_order_history(sleeve_id)
            ledger_state = ledger.reconstruct_from_history(sleeve_row["capital_usd"], order_history)
            positions = {symbol: pos.qty for symbol, pos in ledger_state.positions.items()}
            envelope_dict = json.loads(sleeve_row.get("envelope_json") or "{}")

            trading_day = now_et.strftime("%Y-%m-%d")
            turnover_used_usd = database.get_daily_turnover_usd(sleeve_id, trading_day)
            symbols = {
                rule["when"]["symbol"]
                for rule in rules
                if isinstance(rule.get("when"), dict) and "symbol" in rule["when"]
            }
            turnover_used_by_symbol = dict.fromkeys(symbols, turnover_used_usd)
            live_mode, live_keys_present = _compute_live_gates(sleeve_row)

            outcomes.extend(
                runner.evaluate_rules(
                    rules=rules,
                    sleeve_row=sleeve_row,
                    sleeve_equity_usd=_book_equity_usd(ledger_state, closes_by_symbol),
                    now_utc=now_utc,
                    closes_by_symbol=closes_by_symbol,
                    positions=positions,
                    fred_cache=fred_cache,
                    envelope=envelope_dict,
                    live_mode=live_mode,
                    live_keys_present=live_keys_present,
                    discord_webhook_url=discord_webhook_url,
                    turnover_used_by_symbol=turnover_used_by_symbol,
                )
            )
        except Exception:
            logger.exception(
                "sleeve %s tick processing failed (rule-evaluation phase); "
                "other sleeves unaffected",
                sleeve_id,
            )
            continue

    return outcomes
