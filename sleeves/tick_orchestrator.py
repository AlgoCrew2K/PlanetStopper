"""sleeves/tick_orchestrator.py -- the P3 engine-tick entry point (AC-9, AC-10).

The single function a fresh-subprocess-per-minute engine tick calls
(``run_sleeve_tick_for_all_sleeves``). For every sleeve it, in order:

  0. SHADOW-status sleeves ALSO get cancel_open_orders_for_shadow_sleeve
     (AC-12 disarm support -- the disarm route can never itself reach
     sleeves.alpaca_orders, so this tick is where a disarmed sleeve's
     lingering open orders actually get cancelled).
  1. Polls the broker for fills on already-acked, non-terminal orders
     (``poll_and_apply_fills``) and records any newly-reported fill. Runs for
     EVERY sleeve, SHADOW included (s3-review BLOCK 1): a disarmed sleeve
     stays SHADOW permanently until re-armed and can still hold real
     residual broker-side exposure (a TOCTOU fill between the disarm click
     and this tick's cancel attempt, or a pre-disarm position) -- "no
     live-armed rules" does not mean "nothing at the broker to track".
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
    sharing the account) is ignored entirely.
  - Per-sleeve cash conservation remains sleeves.ledger's own internal law
    (already enforced there); it is simply no longer checked against the
    broker on a per-sleeve basis.
See sleeves/reconciliation.py's reconcile_aggregate_cash/
reconcile_aggregate_position for the underlying one-sided pure-function
contract.

Known limitation (tracked, non-blocking; no bars/FRED-cache accessor is wired
into the engine tick yet): ``closes_by_symbol``/``fred_cache`` are passed as
empty dicts to ``evaluate_rules``. This is safe by construction, not a silent
gap -- ``sleeves.rules.senses``/``conditions`` fail closed on missing sense
data (``not fireable``, no fire recorded), so a rule referencing a daily-bar
or FRED sense simply never fires until a real data source is wired in a
follow-up cycle. A DEFENSIVE/ENTRY rule that only senses ``sleeve_status``/
``sleeve_equity_usd``/``position_qty``/time-of-day is unaffected.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import requests

import database
import sleeves.alpaca_orders as alpaca_orders
import sleeves.ledger as ledger
import sleeves.reconciliation as reconciliation
import sleeves.rules.runner as runner

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


def poll_and_apply_fills(
    sleeve_id: int, *, live_mode: bool = False, live_keys_present: bool = False
) -> list[dict]:
    """Poll broker-truth status for every non-terminal, post-ack sleeve order.

    A still-RESERVED pre-ack row (``alpaca_order_id`` still NULL) is skipped
    -- there is no broker order id to poll yet. When the broker reports a
    filled quantity beyond what is already recorded in ``sleeve_fills`` for
    that order, the delta is inserted as a new fill row and the order's
    status advances to the broker's own status string verbatim (never an
    invented synonym). Returns the list of newly-inserted fill dicts (empty
    if nothing new).
    """
    new_fills: list[dict] = []
    for order in database.get_sleeve_orders(sleeve_id=sleeve_id, limit=500):
        if not order.get("alpaca_order_id"):
            continue  # pre-ack RESERVED row -- nothing to poll yet
        if order["status"] in _TERMINAL_ORDER_STATUSES:
            continue  # already terminal -- nothing further to poll

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
        already_recorded_qty = sum(
            f["filled_qty"] for f in database.get_fills_for_order(order["id"])
        )
        broker_filled_qty = float(broker_order.get("filled_qty") or 0.0)
        delta_qty = broker_filled_qty - already_recorded_qty

        if delta_qty > 0:
            filled_at = (
                broker_order.get("filled_at") or broker_order.get("updated_at") or _utcnow_iso()
            )
            fill_price = float(broker_order.get("filled_avg_price") or 0.0)
            # Deterministic per (broker order, cumulative broker-reported
            # qty) -- defense-in-depth dedup on top of the delta math above,
            # which is already the load-bearing idempotency mechanism.
            broker_fill_id = f"{order['alpaca_order_id']}:{broker_filled_qty}"
            database.insert_sleeve_fill(
                order_id=order["id"],
                fill_price=fill_price,
                filled_qty=delta_qty,
                filled_at=filled_at,
                broker_fill_id=broker_fill_id,
            )
            new_fills.append(
                {
                    "order_id": order["id"],
                    "symbol": order["symbol"],
                    "filled_qty": delta_qty,
                    "fill_price": fill_price,
                    "filled_at": filled_at,
                }
            )

        broker_status = broker_order.get("status")
        if broker_status:
            database.update_sleeve_order_status(
                order["client_order_id"], broker_status, raw_json=json.dumps(broker_order)
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
    for sleeve_row in sleeve_rows:
        order_history = database.get_sleeve_order_history(sleeve_row["id"])
        ledger_states[sleeve_row["id"]] = ledger.reconstruct_from_history(
            sleeve_row["capital_usd"], order_history
        )

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

    return paused_ids


# ---------------------------------------------------------------------------
# run_sleeve_tick_for_all_sleeves
# ---------------------------------------------------------------------------


def _load_enabled_rules(sleeve_id: int) -> list[dict]:
    """DB rows -> the rule-dict shape sleeves.rules.runner.evaluate_rules
    expects: each row's id/mode merged with its parsed json_doc fields."""
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
        rules.append({"id": row["id"], "mode": row["mode"], **doc})
    return rules


def _book_equity_usd(ledger_state: ledger.LedgerState) -> float:
    """Sleeve equity from the ledger's own conservation law (capital_usd +
    realized_pnl_usd == cash_usd + reserved_usd + sum(cost_basis_usd)) --
    deliberately NOT a broker mark-to-market figure, so computing it needs no
    additional broker round-trip beyond the one aggregate reconciliation
    already performs this same tick. Unrealized gains/losses on open
    positions are not reflected: a rule sized off this figure is sized
    conservatively LOW when the sleeve is sitting on unrealized gains (never
    conservatively high) -- the safe direction for a risk-sizing input.
    """
    return (
        ledger_state.cash_usd
        + ledger_state.reserved_usd
        + sum(p.cost_basis_usd for p in ledger_state.positions.values())
    )


def run_sleeve_tick_for_all_sleeves(
    *, now_utc: datetime, discord_webhook_url: str | None = None
) -> list:
    """The single entry point one engine tick calls for every managed sleeve.

    Per sleeve:
      0. SHADOW-status sleeves ALSO get cancel_open_orders_for_shadow_sleeve
         (AC-12 design correction -- the disarm ROUTE can never itself reach
         sleeves.alpaca_orders, so this tick is where that cancellation
         actually happens). This is an ADDITIONAL step, not a replacement
         for step 1 below (s3-review BLOCK 1): a disarmed sleeve stays
         SHADOW permanently until re-armed and can still hold real
         broker-side exposure, so it is not exempt from fill-polling or
         reconciliation.
      1. poll_and_apply_fills -- runs for every sleeve not already
         PAUSED_RECONCILIATION coming into this tick.
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

            if sleeve_row["status"] == "SHADOW":
                cancel_open_orders_for_shadow_sleeve(
                    sleeve_id,
                    live_mode=live_mode,
                    live_keys_present=live_keys_present,
                    discord_webhook_url=discord_webhook_url,
                )

            if sleeve_row["status"] == "PAUSED_RECONCILIATION":
                already_paused_ids.add(sleeve_id)
                continue

            poll_and_apply_fills(
                sleeve_id, live_mode=live_mode, live_keys_present=live_keys_present
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

    # --- Step 3: rule evaluation for every sleeve not skipped above. -----
    for sleeve_row in all_sleeve_rows:
        sleeve_id = sleeve_row["id"]
        if sleeve_id in skip_rule_eval_ids:
            continue
        try:
            rules = _load_enabled_rules(sleeve_id)
            if not rules:
                continue

            order_history = database.get_sleeve_order_history(sleeve_id)
            ledger_state = ledger.reconstruct_from_history(sleeve_row["capital_usd"], order_history)
            positions = {symbol: pos.qty for symbol, pos in ledger_state.positions.items()}
            envelope_dict = json.loads(sleeve_row.get("envelope_json") or "{}")

            now_et = now_utc.astimezone(_ET)
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
                    sleeve_equity_usd=_book_equity_usd(ledger_state),
                    now_utc=now_utc,
                    closes_by_symbol={},
                    positions=positions,
                    fred_cache={},
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
