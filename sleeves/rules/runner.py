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

    limits_doc = rule.get("limits") or {}
    rule_market_open = market_open if limits_doc.get("market_hours_only", True) else True
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
    shadow = rule["mode"] == "SHADOW"
    fired_at = now_utc.astimezone(_UTC).strftime("%Y-%m-%d %H:%M:%S")

    action_results: list[actions.ActionResult] = []
    fire_ids: list[int] = []
    for action in rule["then"]:
        action_result = actions.dispatch_action(action, ctx=action_ctx, shadow=shadow)
        action_results.append(action_result)

        outcome_dict = {
            "would_have_qty": action_result.would_have_qty,
            "would_have_notional_usd": action_result.would_have_notional_usd,
            "executed": action_result.executed,
            "refused_reason": action_result.refused_reason,
            "order": action_result.order_result.order if action_result.order_result else None,
            "order_error": action_result.order_result.error if action_result.order_result else None,
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
