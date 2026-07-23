"""sleeves/rules/runner.py -- the P2 tick orchestrator (AC-6, AC-10, AC-20).

`evaluate_rules` is the single entry point a fresh-subprocess-per-minute tick
calls: for each rule it senses, evaluates the condition tree (fail-safe on
any unavailable sense), checks pacing (episode latch / cooldown / max fires
per day / market hours), and -- when permitted -- dispatches every `then`
action through actions.dispatch_action, persisting one sleeve_rule_fires row
per action. DEFENSIVE-class rules are fully evaluated and dispatched before
any ENTRY-class rule (same-tick precedence, AC-10/AC-20 spirit); market-open
state is derived internally via market_calendar (XNYS, holiday-aware) --
never accepted as a caller-supplied parameter, so a caller cannot substitute
a wrong/naive weekday-only check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import database
import market_calendar
import sleeves.rules.actions as actions
import sleeves.rules.conditions as conditions
import sleeves.rules.limits as limits
import sleeves.rules.schema as schema
import sleeves.rules.senses as senses

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

# Sleeve statuses under which an armed rule may actually dispatch armed --
# the sleeve-level key of the two-key gate below. (LIVE additionally needs
# the AC-14 env gates to resolve a live host; that is host selection, not
# armed-vs-shadow.)
_ARMED_SLEEVE_STATUSES = ("PAPER", "LIVE")


@dataclass(frozen=True)
class FireOutcome:
    rule_id: int
    rule_class: str
    fired: bool
    reason: str | None
    sensed_snapshot: dict
    action_results: tuple[actions.ActionResult, ...]
    fire_ids: tuple[int, ...]


def _collect_sense_keys(node: dict) -> set[str]:
    op = node.get("op")
    if op == "compare":
        return {node["sense"]}
    if op in ("AND", "OR"):
        keys: set[str] = set()
        for child in node.get("children", []):
            keys |= _collect_sense_keys(child)
        return keys
    if op == "NOT":
        return _collect_sense_keys(node.get("child", {}))
    return set()


def _class_sort_key(rule_id: int, rule_class: str | None) -> tuple:
    # DEFENSIVE rules (group 0) entirely before everything else (group 1,
    # covering ENTRY and any unclassifiable rule_class=None); ascending id
    # within each group.
    is_defensive = rule_class == schema.RULE_CLASS_DEFENSIVE
    return (0 if is_defensive else 1, rule_id)


def _evaluate_one_rule(
    rule: dict,
    *,
    rule_class: str | None,
    sleeve_row: dict,
    sleeve_equity_usd: float,
    now_utc: datetime,
    now_et: datetime,
    market_open: bool,
    closes_by_symbol: dict[str, list[float]],
    positions: dict[str, float],
    fred_cache: dict[str, list[dict]],
    envelope_dict: dict,
    live_mode: bool,
    live_keys_present: bool,
    discord_webhook_url: str | None,
    turnover_used_by_symbol: dict[str, float],
) -> FireOutcome:
    symbol = rule["when"]["symbol"]
    closes = closes_by_symbol.get(symbol, [])
    position_qty = positions.get(symbol, 0.0)
    price = closes[-1] if closes else 0.0

    sense_ctx = senses.SenseContext(
        now_et=now_et,
        sleeve_row={
            "sleeve_status": sleeve_row.get("status"),
            "sleeve_equity_usd": sleeve_equity_usd,
            "position_qty": position_qty,
        },
        closes=closes,
        fred_cache=fred_cache,
        as_of=now_et.date(),
    )
    sense_keys = _collect_sense_keys(rule["if"])
    sensed = {key: senses.resolve_sense(key, ctx=sense_ctx) for key in sense_keys}
    sensed_snapshot = {key: result.value for key, result in sensed.items()}

    eval_result = conditions.evaluate_condition(rule["if"], sensed)

    if eval_result.reason is not None:
        # Fail-safe: missing sense data. Mirrors the market-closed contract --
        # nothing is reliably known this tick, so pacing state is left
        # entirely untouched (no rearm/latch bookkeeping perturbed) and no
        # fire row is written.
        return FireOutcome(
            rule["id"], rule_class or "", False, eval_result.reason, sensed_snapshot, (), ()
        )

    # AC-11 churn brake -- ENTRY rules only, checked before pacing so a
    # benched rule neither latches an episode nor records a fire. DEFENSIVE
    # (and unclassifiable) rules never route here: exits are never benched
    # (AC-10/AC-11) -- insurance must not be silenced by its own cost.
    if rule_class == schema.RULE_CLASS_ENTRY and eval_result.fireable:
        bench_reason = limits.check_and_engage_churn_brake(
            rule_id=rule["id"],
            sleeve_id=rule["sleeve_id"],
            now_utc=now_utc,
            capital_usd=sleeve_row["capital_usd"],
            discord_webhook_url=discord_webhook_url,
        )
        if bench_reason is not None:
            return FireOutcome(
                rule["id"], rule_class or "", False, bench_reason, sensed_snapshot, (), ()
            )

    limits_doc = rule.get("limits") or {}
    rule_market_open = market_open if limits_doc.get("market_hours_only", True) else True
    # Snapshot BEFORE the pacing check so a dispatch crash that produced
    # nothing can compensate the consumed episode (audit 2026-07-09 #9).
    pacing_snapshot = limits.snapshot_pacing_state(rule["id"])
    pacing_result = limits.check_and_advance_pacing(
        rule_id=rule["id"],
        now_utc=now_utc,
        market_open=rule_market_open,
        condition_true=eval_result.fireable,
        cooldown_sec=limits_doc.get("cooldown_sec"),
        max_fires_per_day=limits_doc.get("max_fires_per_day"),
        rearm_ticks=limits_doc.get("rearm_ticks", limits.DEFAULT_REARM_TICKS),
    )

    if not pacing_result.fireable:
        return FireOutcome(
            rule["id"], rule_class or "", False, pacing_result.reason, sensed_snapshot, (), ()
        )

    action_ctx = actions.ActionContext(
        sleeve_id=rule["sleeve_id"],
        rule_id=rule["id"],
        symbol=symbol,
        price=price,
        sleeve_equity_usd=sleeve_equity_usd,
        capital_usd=sleeve_row["capital_usd"],
        current_position_qty=position_qty,
        turnover_used_usd=turnover_used_by_symbol.get(symbol, 0.0),
        envelope=envelope_dict,
        live_mode=live_mode,
        live_keys_present=live_keys_present,
        discord_webhook_url=discord_webhook_url,
    )
    # Two-key armed-dispatch gate (ratified defense-in-depth, 2026-07-09):
    # an armed dispatch requires the RULE mode (arm ceremony) AND the SLEEVE
    # status (route promotion) to agree. A drift state -- a PAPER/LIVE rule
    # inside a SHADOW-status sleeve, unreachable via routes but seedable by
    # DB drift -- fails toward shadow: evaluate, record, place nothing.
    # Placing an order there is exactly the audit #3/#4 money-loser (step-0
    # cleanup cancels the armed sleeve's own orders every tick).
    sleeve_armed = sleeve_row.get("status") in _ARMED_SLEEVE_STATUSES
    shadow = rule["mode"] == "SHADOW" or not sleeve_armed
    fired_at = now_utc.astimezone(_UTC).strftime("%Y-%m-%d %H:%M:%S")

    # DB-grounded evidence baseline for the crash-compensation decision
    # below (reviewer finding SF-R-1): runner-local containers (fire_ids /
    # action_results) lose their evidence when dispatch crashes INSIDE
    # dispatch_action after the broker submit -- e.g. the post-ack
    # attach_alpaca_order_id write raising -- but the sleeve_orders rows do
    # not: the RESERVED insert precedes every broker submit, so any row for
    # this rule that is not in this baseline proves an order path STARTED,
    # whether or not the broker call itself completed.
    pre_dispatch_order_ids = {
        order["id"]
        for order in database.get_sleeve_orders(sleeve_id=rule["sleeve_id"], limit=500)
        if order.get("rule_id") == rule["id"]
    }

    action_results: list[actions.ActionResult] = []
    fire_ids: list[int] = []
    try:
        for action in rule["then"]:
            action_result = actions.dispatch_action(action, ctx=action_ctx, shadow=shadow)
            action_results.append(action_result)

            outcome_dict = {
                "would_have_qty": action_result.would_have_qty,
                "would_have_notional_usd": action_result.would_have_notional_usd,
                "executed": action_result.executed,
                "refused_reason": action_result.refused_reason,
                "order": action_result.order_result.order if action_result.order_result else None,
                "order_error": (
                    action_result.order_result.error if action_result.order_result else None
                ),
            }
            clamp = action_result.clamp
            fire_id = database.insert_sleeve_rule_fire(
                rule_id=rule["id"],
                sleeve_id=rule["sleeve_id"],
                action=action["type"],
                rule_class=rule_class or "",
                mode_at_fire=rule["mode"],
                sensed_snapshot_json=json.dumps(sensed_snapshot),
                outcome_json=json.dumps(outcome_dict),
                clamped=bool(clamp.clamped) if clamp else False,
                clamp_reason=clamp.reason if clamp else None,
                episode_id=pacing_result.episode_id,
                # AC-19: an armed, executed action's real sleeve_orders.id
                # (None for shadow / refused / no-order actions).
                order_id=action_result.order_id,
                fired_at=fired_at,
            )
            fire_ids.append(fire_id)
    except Exception:
        # Audit 2026-07-09 #9 + reviewer finding SF-R-1: the pacing episode
        # was latched above, BEFORE dispatch -- a crash here with a
        # still-true condition would silence the rule permanently (rearm
        # needs consecutive FALSE ticks). Compensate ONLY when the crashed
        # tick provably produced nothing: no fire row recorded, no
        # ActionResult carrying an order, AND no new sleeve_orders row for
        # this rule in the DB. The DB check is the load-bearing one
        # (SF-R-1): a crash inside dispatch_action after the broker submit
        # discards the runner-local evidence, but the RESERVED row inserted
        # before every submit survives -- if it exists, the broker MAY hold
        # the order, and restoring the episode would re-fire next tick into
        # a SECOND live order. Fail closed: when evidence is present or
        # unknowable, eat the lost episode, never risk a doubled order.
        if not fire_ids and not any(r.order_id is not None for r in action_results):
            try:
                order_path_started = any(
                    order["id"] not in pre_dispatch_order_ids
                    for order in database.get_sleeve_orders(sleeve_id=rule["sleeve_id"], limit=500)
                    if order.get("rule_id") == rule["id"]
                )
            except Exception:
                # Cannot prove absence of an order -> keep the episode.
                order_path_started = True
            if not order_path_started:
                limits.restore_pacing_state(rule["id"], pacing_snapshot)
        raise

    return FireOutcome(
        rule["id"],
        rule_class or "",
        True,
        None,
        sensed_snapshot,
        tuple(action_results),
        tuple(fire_ids),
    )


def evaluate_rules(
    *,
    rules: list[dict],
    sleeve_row: dict,
    sleeve_equity_usd: float,
    now_utc: datetime,
    closes_by_symbol: dict[str, list[float]],
    positions: dict[str, float],
    fred_cache: dict[str, list[dict]],
    envelope: dict,
    live_mode: bool = False,
    live_keys_present: bool = False,
    discord_webhook_url: str | None = None,
    turnover_used_by_symbol: dict[str, float] | None = None,
) -> list[FireOutcome]:
    now_et = now_utc.astimezone(_ET)
    market_open = market_calendar.get_market_state(now_et) == "open"
    turnover_map = turnover_used_by_symbol or {}

    rules_with_class = [
        (rule, schema.derive_rule_class([a["type"] for a in rule["then"]])) for rule in rules
    ]
    rules_with_class.sort(key=lambda pair: _class_sort_key(pair[0]["id"], pair[1]))

    return [
        _evaluate_one_rule(
            rule,
            rule_class=rule_class,
            sleeve_row=sleeve_row,
            sleeve_equity_usd=sleeve_equity_usd,
            now_utc=now_utc,
            now_et=now_et,
            market_open=market_open,
            closes_by_symbol=closes_by_symbol,
            positions=positions,
            fred_cache=fred_cache,
            envelope_dict=envelope,
            live_mode=live_mode,
            live_keys_present=live_keys_present,
            discord_webhook_url=discord_webhook_url,
            turnover_used_by_symbol=turnover_map,
        )
        for rule, rule_class in rules_with_class
    ]
