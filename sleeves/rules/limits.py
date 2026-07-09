"""sleeves/rules/limits.py -- pacing state machine (AC-5, episode latch) and
the AC-11 churn brake.

ALL pacing/bench state lives in `sleeve_runtime` (AC-5 -- the engine is a
fresh subprocess per minute), via the P1 `database.get_sleeve_runtime`/
`set_sleeve_runtime` accessors, keyed per rule_id. `sleeve_rule_fires` is the
durable AUDIT log the runner writes to after a permitted fire; it is NOT
this function's own pacing source -- this function never reads or writes
`sleeve_rule_fires` at all. It holds no in-process cache: every call re-reads
whatever it needs from string-valued sleeve_runtime keys (last_fire_ts,
fires_today_date, fires_today_count, episode_latched, consecutive_false_count,
benched_date, benched_reason).

Algorithm pinned exactly per tests/sleeves/rules/test_limits_pacing.py's
module docstring contract; see that file for the golden-fixture trace this
implementation is verified against.

Churn brake (AC-11, audit 2026-07-09 #17 "not built"): an ENTRY rule with
>= CHURN_BENCH_MIN_ROUND_TRIPS same-day round trips at a meaningful net loss
is benched for the ET trading day. Bench state is keyed by the trading-day
string, so "auto re-arm next trading day" needs no un-bench machinery -- a
new day simply no longer matches. Routing the check to ENTRY rules only
(exits are never benched, AC-10/AC-11) is the RUNNER's responsibility -- it
is the layer that derives rule_class.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import database

_ET = ZoneInfo("America/New_York")

_REASON_MARKET_CLOSED = "market_closed"
_REASON_LATCHED = "episode_latched"
_REASON_COOLDOWN = "cooldown_active"
_REASON_MAX_FIRES = "max_fires_per_day_reached"
_REASON_CHURN_BENCHED = "churn_benched"
DEFAULT_REARM_TICKS = 3

_KEY_LAST_FIRE_TS = "last_fire_ts"
_KEY_FIRES_TODAY_DATE = "fires_today_date"
_KEY_FIRES_TODAY_COUNT = "fires_today_count"
_KEY_EPISODE_LATCHED = "episode_latched"
_KEY_CONSECUTIVE_FALSE = "consecutive_false_count"

# PUBLIC sleeve_runtime keys -- the panel/digest read bench/stale state
# through these (and is_rule_benched below); the key strings are a
# cross-module contract, not this module's private vocabulary.
BENCHED_DATE_KEY = "benched_date"
BENCHED_REASON_KEY = "benched_reason"
# Written by sleeves/tick_orchestrator.py each tick: "1" when the rule's
# symbol produced no bars while other symbols did (delisted/renamed), "0"
# on recovery. Read by the panel's rule-level stale flag.
STALE_NO_BARS_KEY = "stale_no_bars"

# Every key check_and_advance_pacing reads or writes -- the unit of the
# snapshot/restore compensation below (audit 2026-07-09 #9).
_PACING_STATE_KEYS = (
    _KEY_LAST_FIRE_TS,
    _KEY_FIRES_TODAY_DATE,
    _KEY_FIRES_TODAY_COUNT,
    _KEY_EPISODE_LATCHED,
    _KEY_CONSECUTIVE_FALSE,
)

# AC-11 verbatim: ">=5 same-day round trips at a meaningful net loss".
CHURN_BENCH_MIN_ROUND_TRIPS = 5
# "Meaningful" threshold -- operational policy (the AC deliberately leaves
# the number to implementation): net same-day realized loss of at least
# 0.1% of the sleeve's fixed capital. Small enough that genuine churn (the
# audit's canonical scenario loses 5% of capital) always trips it, large
# enough that fee/slippage-scale noise on an otherwise flat day does not.
CHURN_MEANINGFUL_LOSS_PCT_OF_CAPITAL = 0.001

# Matches sleeves/rules/actions.py's _NOTIFY_REQUEST_TIMEOUT_S -- same
# never-hang-the-tick-on-Discord convention, same constant value.
_BENCH_NOTE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class PacingResult:
    fireable: bool
    reason: str | None
    episode_id: str | None


def _trading_day(now_utc: datetime) -> str:
    """ET trading-day boundary, derived from the caller-supplied tick instant
    -- never naive UTC/local "today" (matches get_daily_turnover_usd's
    established caller-supplied-trading_day convention)."""
    return now_utc.astimezone(_ET).date().isoformat()


def check_and_advance_pacing(
    *,
    rule_id: int,
    now_utc: datetime,
    market_open: bool,
    condition_true: bool,
    cooldown_sec: int | None = None,
    max_fires_per_day: int | None = None,
    rearm_ticks: int = DEFAULT_REARM_TICKS,
) -> PacingResult:
    if not market_open:
        # No state read or written at all -- a closed-market tick must not
        # perturb the rearm counter or anything else.
        return PacingResult(fireable=False, reason=_REASON_MARKET_CLOSED, episode_id=None)

    episode_latched = database.get_sleeve_runtime(rule_id, _KEY_EPISODE_LATCHED)
    consecutive_false_raw = database.get_sleeve_runtime(rule_id, _KEY_CONSECUTIVE_FALSE)
    consecutive_false = int(consecutive_false_raw) if consecutive_false_raw else 0

    if not condition_true:
        consecutive_false += 1
        if episode_latched and consecutive_false >= rearm_ticks:
            database.set_sleeve_runtime(rule_id, _KEY_EPISODE_LATCHED, "")
            database.set_sleeve_runtime(rule_id, _KEY_CONSECUTIVE_FALSE, "0")
        else:
            database.set_sleeve_runtime(rule_id, _KEY_CONSECUTIVE_FALSE, str(consecutive_false))
        return PacingResult(fireable=False, reason=None, episode_id=None)

    # condition_true is True: a true tick always breaks the false streak,
    # latched or not.
    database.set_sleeve_runtime(rule_id, _KEY_CONSECUTIVE_FALSE, "0")

    if episode_latched:
        return PacingResult(fireable=False, reason=_REASON_LATCHED, episode_id=None)

    last_fire_ts = database.get_sleeve_runtime(rule_id, _KEY_LAST_FIRE_TS)
    if cooldown_sec is not None and last_fire_ts:
        elapsed = (now_utc - datetime.fromisoformat(last_fire_ts)).total_seconds()
        if elapsed < cooldown_sec:
            return PacingResult(fireable=False, reason=_REASON_COOLDOWN, episode_id=None)

    trading_day = _trading_day(now_utc)
    fires_today_date = database.get_sleeve_runtime(rule_id, _KEY_FIRES_TODAY_DATE)
    fires_today_count_raw = database.get_sleeve_runtime(rule_id, _KEY_FIRES_TODAY_COUNT)
    today_count = (
        int(fires_today_count_raw)
        if fires_today_date == trading_day and fires_today_count_raw
        else 0
    )

    if max_fires_per_day is not None and today_count >= max_fires_per_day:
        return PacingResult(fireable=False, reason=_REASON_MAX_FIRES, episode_id=None)

    new_episode_id = uuid.uuid4().hex
    database.set_sleeve_runtime(rule_id, _KEY_LAST_FIRE_TS, now_utc.isoformat())
    database.set_sleeve_runtime(rule_id, _KEY_FIRES_TODAY_DATE, trading_day)
    database.set_sleeve_runtime(rule_id, _KEY_FIRES_TODAY_COUNT, str(today_count + 1))
    database.set_sleeve_runtime(rule_id, _KEY_EPISODE_LATCHED, new_episode_id)
    return PacingResult(fireable=True, reason=None, episode_id=new_episode_id)


# ---------------------------------------------------------------------------
# Pacing compensation (audit 2026-07-09 #9): check_and_advance_pacing latches
# the episode BEFORE the runner dispatches actions, so a dispatch crash that
# recorded NOTHING left the rule latched with zero fires -- and a still-true
# condition never rearms (rearm needs consecutive FALSE ticks), permanently
# silencing the rule. The runner snapshots pacing state before the check and
# restores it when a crash consumed the episode without producing any fire
# row or broker order.
# ---------------------------------------------------------------------------


def snapshot_pacing_state(rule_id: int) -> dict:
    """Raw value of every pacing key, taken BEFORE check_and_advance_pacing
    so a zero-outcome dispatch crash can be compensated (audit #9). A key
    never written reads as None."""
    return {key: database.get_sleeve_runtime(rule_id, key) for key in _PACING_STATE_KEYS}


def restore_pacing_state(rule_id: int, snapshot: dict) -> None:
    """Compensation write-back of a snapshot_pacing_state() result: pacing-
    wise, the crashed tick never happened. A None (never-written) value
    restores as "" -- every pacing reader treats empty and absent
    identically (falsy latch, zero counts)."""
    for key, value in snapshot.items():
        database.set_sleeve_runtime(rule_id, key, value or "")


# ---------------------------------------------------------------------------
# AC-11 churn brake
# ---------------------------------------------------------------------------


def _et_trading_day_of_fill(filled_at: str | None) -> str | None:
    """ET trading day of one fill's UTC timestamp; None when unparseable
    (an unparseable fill is EXCLUDED from the churn fold -- garbage data
    must not bench a healthy rule)."""
    if not filled_at:
        return None
    try:
        parsed = datetime.fromisoformat(filled_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Stored timestamps are UTC by convention (runner.py/_utcnow_iso);
        # a naive value is interpreted the same way, never as local time.
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(_ET).date().isoformat()


def _same_day_round_trips_and_realized_usd(
    order_history: list[dict], rule_id: int, trading_day: str
) -> tuple[int, float]:
    """Flat-to-flat fold of ONE rule's own fills on ONE ET trading day.

    A round trip completes each time the rule's own intra-day position
    (buys minus sells, this rule's fills only, chronological) returns to
    flat from above -- robust to partial fills and varying quantities,
    matching the plain reading of AC-11's "same-day round trips". Realized
    P&L uses the fold's own rule-scoped average cost. A sell with no
    same-day buy basis (closing a PRIOR day's position) contributes to
    neither count nor loss: that is an exit, and exits are never the churn
    being braked.
    """
    events: list[tuple[str, str, float, float]] = []
    for order in order_history:
        if order.get("rule_id") != rule_id:
            continue
        side = order.get("side")
        for fill in order.get("fills") or []:
            if _et_trading_day_of_fill(fill.get("filled_at")) != trading_day:
                continue
            events.append((fill["filled_at"], side, fill["filled_qty"], fill["fill_price"]))
    events.sort(key=lambda event: event[0])

    position_qty = 0.0
    open_cost_usd = 0.0
    realized_usd = 0.0
    round_trips = 0
    for _filled_at, side, qty, price in events:
        if side == "buy":
            position_qty += qty
            open_cost_usd += qty * price
        elif side == "sell" and position_qty > 0:
            matched_qty = min(qty, position_qty)
            avg_cost = open_cost_usd / position_qty
            realized_usd += matched_qty * (price - avg_cost)
            open_cost_usd -= matched_qty * avg_cost
            position_qty -= matched_qty
            if position_qty <= 1e-9:  # float dust from partial-fill arithmetic
                position_qty = 0.0
                open_cost_usd = 0.0
                round_trips += 1
    return round_trips, realized_usd


def _post_bench_note(
    discord_webhook_url: str | None, rule_id: int, sleeve_id: int, trading_day: str, reason: str
) -> None:
    """Best-effort Discord note on bench engagement (AC-11: the operator
    must learn their rule was silenced for the day, and why) -- never raises,
    mirrors sleeves/rules/actions.py's notify dispatch convention."""
    if not discord_webhook_url:
        return
    with contextlib.suppress(requests.RequestException):
        requests.post(
            discord_webhook_url,
            json={
                "content": (
                    f"Churn brake: rule {rule_id} (sleeve {sleeve_id}) BENCHED for "
                    f"{trading_day} -- {reason}. Auto re-arms next trading day; "
                    f"exits are unaffected."
                )
            },
            timeout=_BENCH_NOTE_TIMEOUT_S,
        )


def is_rule_benched(rule_id: int, *, now_utc: datetime) -> bool:
    """True iff the rule's bench is engaged for now_utc's ET trading day.
    The panel/digest read bench state through this (plus BENCHED_REASON_KEY
    for the note text) -- a stale benched_date from a previous day is simply
    not benched, which IS the AC-11 auto re-arm."""
    return database.get_sleeve_runtime(rule_id, BENCHED_DATE_KEY) == _trading_day(now_utc)


def check_and_engage_churn_brake(
    *,
    rule_id: int,
    sleeve_id: int,
    now_utc: datetime,
    capital_usd: float,
    discord_webhook_url: str | None = None,
) -> str | None:
    """AC-11 churn brake for one ENTRY rule on one tick.

    Returns the refusal reason ("churn_benched") when the rule is -- or on
    this call becomes -- benched for now_utc's ET trading day; None when
    clear to proceed. The bench engages when the rule's own same-day
    round-trip history reaches CHURN_BENCH_MIN_ROUND_TRIPS completed round
    trips AND a net realized loss of at least
    CHURN_MEANINGFUL_LOSS_PCT_OF_CAPITAL of the sleeve's capital;
    engagement writes the durable bench keys (restart-safe per AC-5) and
    posts one Discord note. The already-benched short-circuit means the
    history fold runs at most once per (rule, day) after engagement.

    The caller (runner) routes ONLY ENTRY-class rules here -- DEFENSIVE
    rules are never benched (AC-10/AC-11), insurance must not be silenced
    by its own cost.
    """
    trading_day = _trading_day(now_utc)
    if database.get_sleeve_runtime(rule_id, BENCHED_DATE_KEY) == trading_day:
        return _REASON_CHURN_BENCHED

    order_history = database.get_sleeve_order_history(sleeve_id)
    round_trips, realized_usd = _same_day_round_trips_and_realized_usd(
        order_history, rule_id, trading_day
    )
    meaningful_loss_usd = CHURN_MEANINGFUL_LOSS_PCT_OF_CAPITAL * capital_usd
    if round_trips >= CHURN_BENCH_MIN_ROUND_TRIPS and realized_usd <= -meaningful_loss_usd:
        reason = (
            f"{round_trips} same-day round trips at net realized "
            f"${realized_usd:+,.2f} (meaningful-loss threshold "
            f"${-meaningful_loss_usd:,.2f})"
        )
        database.set_sleeve_runtime(rule_id, BENCHED_DATE_KEY, trading_day)
        database.set_sleeve_runtime(rule_id, BENCHED_REASON_KEY, reason)
        _post_bench_note(discord_webhook_url, rule_id, sleeve_id, trading_day, reason)
        return _REASON_CHURN_BENCHED
    return None
