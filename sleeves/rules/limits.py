"""sleeves/rules/limits.py -- pacing state machine (AC-5, episode latch).

ALL pacing state lives in `sleeve_runtime` (AC-5 -- the engine is a fresh
subprocess per minute), via the P1 `database.get_sleeve_runtime`/
`set_sleeve_runtime` accessors, keyed per rule_id. `sleeve_rule_fires` is the
durable AUDIT log the runner writes to after a permitted fire; it is NOT
this function's own pacing source -- this function never reads or writes
`sleeve_rule_fires` at all. It holds no in-process cache: every call re-reads
whatever it needs from five string-valued sleeve_runtime keys (last_fire_ts,
fires_today_date, fires_today_count, episode_latched, consecutive_false_count).

Algorithm pinned exactly per tests/sleeves/rules/test_limits_pacing.py's
module docstring contract; see that file for the golden-fixture trace this
implementation is verified against.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import database

_ET = ZoneInfo("America/New_York")

_REASON_MARKET_CLOSED = "market_closed"
_REASON_LATCHED = "episode_latched"
_REASON_COOLDOWN = "cooldown_active"
_REASON_MAX_FIRES = "max_fires_per_day_reached"
DEFAULT_REARM_TICKS = 3

_KEY_LAST_FIRE_TS = "last_fire_ts"
_KEY_FIRES_TODAY_DATE = "fires_today_date"
_KEY_FIRES_TODAY_COUNT = "fires_today_count"
_KEY_EPISODE_LATCHED = "episode_latched"
_KEY_CONSECUTIVE_FALSE = "consecutive_false_count"


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
