"""sleeves/rules/senses.py -- the closed sense registry (AC-4).

Every sense_* function is a pure, never-raising computation: missing or
insufficient input is a fail-safe SenseResult(available=False, reason=...),
never an exception. `resolve_sense` parses a schema-validated sense-key
string (see schema.py's grammar) and dispatches to the right sense_*
function.

Indicator formulas are pinned exactly per the AC-4 contract in
tests/sleeves/rules/test_senses.py's module docstring (momentum matches
advisors.lens_technicals._compute_momentum; realized_vol matches
regime_classifier._realized_vol applied to returns).

FRED sensing is cache-read-only by construction -- this module never
imports `requests` (a live FRED call would defeat the "cached values only,
never live in the tick" plan decision); a structural AST test pins this.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime

_DAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

_SLEEVE_STATE_FIELDS = frozenset(
    {"sleeve_status", "sleeve_cash_usd", "sleeve_equity_usd", "position_qty"}
)

# Mirrors schema.py's indicator sense-key grammar: "<indicator>_<window>".
_INDICATOR_KEY_RE = re.compile(r"^(sma|ema|rsi|momentum|realized_vol|drawdown_from_high)_(\d+)$")

_REASON_NAIVE_OR_WRONG_TZ = "naive_or_wrong_tz_datetime"
_REASON_FIELD_MISSING = "field_missing"
_REASON_INSUFFICIENT_HISTORY = "insufficient_history"
_REASON_NO_CACHED_OBSERVATIONS = "no_cached_observations"
_REASON_STALE = "stale"
_REASON_UNKNOWN_INDICATOR = "unknown_indicator"
_REASON_UNKNOWN_SENSE_KEY = "unknown_sense_key"


@dataclass(frozen=True)
class SenseResult:
    value: float | str | int | None
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class SenseContext:
    now_et: datetime
    sleeve_row: dict
    closes: list[float]
    fred_cache: dict[str, list[dict]]
    as_of: date


def _unavailable(reason: str) -> SenseResult:
    return SenseResult(value=None, available=False, reason=reason)


def _is_et_wall_clock(now_et: datetime) -> bool:
    if now_et.tzinfo is None or now_et.utcoffset() is None:
        return False
    return getattr(now_et.tzinfo, "key", None) == "America/New_York"


def sense_time_of_day(now_et: datetime) -> SenseResult:
    if not _is_et_wall_clock(now_et):
        return _unavailable(_REASON_NAIVE_OR_WRONG_TZ)
    seconds = now_et.hour * 3600 + now_et.minute * 60 + now_et.second
    return SenseResult(value=seconds, available=True)


def sense_day_of_week(now_et: datetime) -> SenseResult:
    if not _is_et_wall_clock(now_et):
        return _unavailable(_REASON_NAIVE_OR_WRONG_TZ)
    return SenseResult(value=_DAY_CODES[now_et.weekday()], available=True)


def sense_sleeve_state(*, sleeve_row: dict, field: str) -> SenseResult:
    if sleeve_row.get(field) is None:
        return _unavailable(_REASON_FIELD_MISSING)
    return SenseResult(value=sleeve_row[field], available=True)


def sense_indicator(*, indicator: str, closes: list[float], window: int) -> SenseResult:
    if indicator in ("sma", "ema", "drawdown_from_high"):
        if len(closes) < window:
            return _unavailable(_REASON_INSUFFICIENT_HISTORY)
    elif indicator in ("rsi", "momentum", "realized_vol"):
        if len(closes) < window + 1:
            return _unavailable(_REASON_INSUFFICIENT_HISTORY)
    else:
        return _unavailable(_REASON_UNKNOWN_INDICATOR)

    if indicator == "sma":
        return SenseResult(value=sum(closes[-window:]) / window, available=True)

    if indicator == "ema":
        seed = sum(closes[:window]) / window
        alpha = 2.0 / (window + 1)
        ema = seed
        for c in closes[window:]:
            ema = c * alpha + ema * (1 - alpha)
        return SenseResult(value=ema, available=True)

    if indicator == "rsi":
        recent = closes[-(window + 1) :]
        deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
        gains = [d for d in deltas if d > 0]
        losses = [abs(d) for d in deltas if d < 0]
        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window
        if avg_loss == 0:
            value = 100.0  # never divide by zero -- zero losses means maximally overbought
        elif avg_gain == 0:
            value = 0.0
        else:
            rs = avg_gain / avg_loss
            value = 100 - 100 / (1 + rs)
        return SenseResult(value=value, available=True)

    if indicator == "momentum":
        needed = window + 1
        value = (closes[-1] - closes[-needed]) / closes[-needed]
        return SenseResult(value=value, available=True)

    if indicator == "realized_vol":
        recent = closes[-(window + 1) :]
        returns = [(recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent))]
        n = len(returns)
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)  # sample stdev, ddof=1
        return SenseResult(value=math.sqrt(variance), available=True)

    # drawdown_from_high
    sl = closes[-window:]
    running_high = max(sl)
    return SenseResult(value=(sl[-1] - running_high) / running_high, available=True)


def sense_fred_series(
    *, series_id: str, cached_observations: list[dict], as_of: date, max_age_days: int = 10
) -> SenseResult:
    if not cached_observations:
        return _unavailable(_REASON_NO_CACHED_OBSERVATIONS)
    latest = max(cached_observations, key=lambda obs: obs["date"])
    latest_date = date.fromisoformat(latest["date"])
    if (as_of - latest_date).days > max_age_days:
        return _unavailable(_REASON_STALE)
    return SenseResult(value=latest["value"], available=True)


def resolve_sense(sense_key: str, *, ctx: SenseContext) -> SenseResult:
    if sense_key == "time_of_day":
        return sense_time_of_day(ctx.now_et)
    if sense_key == "day_of_week":
        return sense_day_of_week(ctx.now_et)
    if sense_key in _SLEEVE_STATE_FIELDS:
        return sense_sleeve_state(sleeve_row=ctx.sleeve_row, field=sense_key)

    indicator_match = _INDICATOR_KEY_RE.match(sense_key)
    if indicator_match:
        indicator, window_str = indicator_match.group(1), indicator_match.group(2)
        return sense_indicator(indicator=indicator, closes=ctx.closes, window=int(window_str))

    if sense_key.startswith("fred_"):
        series_id = sense_key[len("fred_") :]
        cached = ctx.fred_cache.get(series_id, [])
        return sense_fred_series(series_id=series_id, cached_observations=cached, as_of=ctx.as_of)

    return _unavailable(f"{_REASON_UNKNOWN_SENSE_KEY}:{sense_key}")
