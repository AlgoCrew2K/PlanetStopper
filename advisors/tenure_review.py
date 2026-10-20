"""Symphony tenure review: advisory compute (cycle C1).

For every live symphony this module assembles six labelled evidence items
(performance, composite, redundancy, backtest, marginal, long-VIX co-firing) and
a verdict (retire-candidate, watch, keep or no verdict). It is pure compute: no
network, no database write, no Composer client and no order, invest, deploy or
liquidation path. Callers fetch the inputs and persist the run record.

Percent scale everywhere: a return of 0.5 means +0.5 percent. Max drawdown is a
percent number that is <= 0. ``None`` means unknown or not computable and is never
replaced by 0; a computed 0 stays 0.
"""

from __future__ import annotations

import itertools
import math
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import analytics
import market_calendar
from advisors import frontrunner_detector, retirement_recommender
from advisors.composer_backtest_client import BacktestResult
from advisors.correlation_diagnostic import PairResult, _extract_aligned_pairs, _pearson_r

# Default minimum tenure, in NYSE trading days, before a symphony gets a verdict
# (about six months of sessions: 126 = 252 / 2, half of a 252-session trading year).
DEFAULT_MIN_TENURE_TRADING_DAYS: int = 126

# Environment key an operator sets to override the default tenure threshold.
TENURE_REVIEW_MIN_TRADING_DAYS_ENV_NAME = "TENURE_REVIEW_MIN_TRADING_DAYS"
TENURE_THRESHOLD_ENV_KEY: str = TENURE_REVIEW_MIN_TRADING_DAYS_ENV_NAME

# A symphony whose backtest of today's definition beats its realized total return
# by more than this many percentage points is flagged "watch" (operator decision;
# unit: percentage points of total return).
BACKTEST_TRAIL_WATCH_PP: float = 10.0

# Long-volatility VIX products: the frontrunner detector's VIX family without the
# short products SVXY and SVIX (inverse products profit when volatility falls).
LONG_VIX_TICKERS: frozenset[str] = frontrunner_detector.VIX_FAMILY_TICKERS - {"SVXY", "SVIX"}

# advisor_role of the persisted run record row.
TENURE_REVIEW_RUN_ROLE: str = "TENURE_REVIEW_RUN"

# Trading sessions per year, the annualization basis used by quantstats (unit: days).
_FRACTION_TO_PERCENT: float = 100.0

# Earliest date the calendar walk goes back to, bounding an absurd threshold setting.
_CALENDAR_FLOOR: date = date(1990, 1, 1)

_ET = ZoneInfo("America/New_York")

INVALID_THRESHOLD_NOTE = "configured threshold invalid; 126 in use"

COMPOSER_BASIS_LABEL = "realized: Composer account, deposit-adjusted"
SHADOW_BASIS_LABEL = "PlanetStopper shadow history from the 2026-06-22 go-live floor"
_NO_BASIS_LABEL = "no history"
BACKTEST_LABEL = "counterfactual: Composer backtest of today's definition"
MARGINAL_LABEL = "counterfactual: portfolio without this symphony"
LONG_VIX_LABEL = "informational: not used in the verdict"

_COMPOSER_SOURCE = "Composer account portfolio history (deposit-adjusted)"
_SHADOW_SOURCE = "PlanetStopper shadow_history"

_FAILURE_REASONS: dict[str, str] = {
    "refused": "Composer refused the request",
    "not_found": "Composer has no series for this symphony",
    "rate_limited": "Composer rate limit",
    "unavailable": "Composer unavailable",
    "empty": "Composer series empty",
    "unreadable": "Composer response unreadable",
}
_EMPTY_SERIES_REASON = _FAILURE_REASONS["empty"]

# The share of aligned days forming the stressed window, as an integer percent so the
# day count is exact (a float product can land on the wrong side of an integer).
_STRESS_PERCENT: int = round(retirement_recommender.STRESS_WINDOW_FRACTION * 100)

EVIDENCE_ITEM_ORDER: tuple[str, ...] = (
    "performance",
    "composite",
    "redundancy",
    "backtest",
    "marginal",
    "long_vix",
)

_PERF_FIGURES = (
    ("total_return", "percent"),
    ("annualized_return", "percent"),
    ("max_drawdown", "percent"),
    ("sharpe", "ratio (daily, non-annualized)"),
    ("sortino", "ratio (daily, non-annualized)"),
    ("calmar", "ratio"),
)
_COMPOSITE_FIGURES = (("composite", "score (0 to 1, fleet-normalized)"),)
_BACKTEST_FIGURES = (
    ("realized_total_return", "percent"),
    ("realized_max_drawdown", "percent"),
    ("backtest_total_return", "percent"),
    ("backtest_max_drawdown", "percent"),
    ("total_return_gap", "percentage points"),
    ("max_drawdown_gap", "percentage points"),
)
_MARGINAL_FIGURES = (
    ("with_total_return", "percent"),
    ("with_max_drawdown", "percent"),
    ("without_total_return", "percent"),
    ("without_max_drawdown", "percent"),
    ("total_return_difference", "percentage points"),
    ("max_drawdown_difference", "percentage points"),
)
_LONG_VIX_FIGURES = (
    ("held_days", "days"),
    ("co_held_days", "days"),
    ("peak_share_with", "percent"),
    ("peak_share_without", "percent"),
)

REASON_NOT_YET_ELIGIBLE = "not yet eligible"
REASON_NO_HISTORY = "no history"
_NO_EVIDENCE_TEXT = "no verdict: no evidence available"


# ---------------------------------------------------------------------------
# Input and result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdSetting:
    value: int
    note: str | None


@dataclass
class SymphonyInput:
    symphony_id: str
    display_name: str
    invested_since: str | None = None
    composer_values: Mapping[str, float | None] | None = None
    series_failure: str | None = None
    shadow_returns: Mapping[str, float | None] | None = None
    current_tickers: Sequence[str] | None = None
    backtest: BacktestResult | None = None
    definition_available: bool = True


@dataclass
class HoldingsInput:
    days: Mapping[str, Mapping[str, Mapping[str, float]]]
    failure: str | None = None


@dataclass
class ReviewInputs:
    run_id: str
    trigger: str
    started_at: datetime
    as_of: date
    threshold: ThresholdSetting
    symphonies: Sequence[SymphonyInput]
    holdings: HoldingsInput | None = None


@dataclass
class ReviewResult:
    run_id: str
    trigger: str
    started_at: datetime
    as_of: date
    threshold: ThresholdSetting
    composite_window: dict
    entries: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Threshold, calendar
# ---------------------------------------------------------------------------


def parse_threshold(raw) -> ThresholdSetting:
    """Parse the configured minimum tenure. Never raises."""
    if raw is None:
        return ThresholdSetting(DEFAULT_MIN_TENURE_TRADING_DAYS, None)
    if isinstance(raw, str):
        text = raw.strip()
        if text and all(c in "0123456789" for c in text):
            try:
                value = int(text)
            except ValueError:  # over the interpreter's digit-count limit
                value = 0
            if value >= 1:
                return ThresholdSetting(value, None)
    return ThresholdSetting(DEFAULT_MIN_TENURE_TRADING_DAYS, INVALID_THRESHOLD_NOTE)


@lru_cache(maxsize=64)
def _sessions_in_year(year: int) -> frozenset[date]:
    first = date(year, 1, 1)
    days = (date(year + 1, 1, 1) - first).days
    return frozenset(
        d
        for d in (first + timedelta(days=i) for i in range(days))
        if market_calendar.is_trading_day(d)
    )


def _is_session(d: date) -> bool:
    return d in _sessions_in_year(d.year)


def as_of_trading_day(now_et: datetime) -> date:
    """The latest NYSE session that has closed at ``now_et`` (read in Eastern time)."""
    now = now_et.astimezone(_ET) if now_et.tzinfo is not None else now_et
    today = now.date()
    if _is_session(today) and now.time() >= market_calendar.session_close(today):
        return today
    d = today - timedelta(days=1)
    while not _is_session(d) and d > _CALENDAR_FLOOR:
        d -= timedelta(days=1)
    return d


def count_trading_days(start: date, end: date) -> int:
    """NYSE sessions in [start, end], both ends included; 0 when end < start."""
    if end < start:
        return 0
    total = 0
    for year in range(start.year, end.year + 1):
        total += sum(1 for d in _sessions_in_year(year) if start <= d <= end)
    return total


def _last_sessions(end: date, count: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < count and d > _CALENDAR_FLOOR:
        if _is_session(d):
            out.append(d)
        d -= timedelta(days=1)
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Series and metrics
# ---------------------------------------------------------------------------


def _finite(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _usable_level(value) -> float | None:
    f = _finite(value)
    return f if f is not None and f > 0 else None


def _parse_date(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def realized_window(values: Mapping, start: date, end: date) -> dict:
    """Usable points in [start, end] and the percent returns between consecutive ones."""
    points: dict[date, float] = {}
    for key, raw in values.items():
        d = _parse_date(key)
        level = _usable_level(raw)
        if d is not None and level is not None and start <= d <= end:
            points[d] = level
    ordered = sorted(points)
    returns = [
        (points[b] / points[a] - 1.0) * _FRACTION_TO_PERCENT for a, b in itertools.pairwise(ordered)
    ]
    return {
        "first": ordered[0].isoformat() if ordered else None,
        "last": ordered[-1].isoformat() if ordered else None,
        "days": len(ordered),
        "dates": [d.isoformat() for d in ordered],
        "returns_pct": returns,
    }


def performance_metrics(returns_pct) -> dict:
    """The six performance figures from percent returns (see module docstring)."""
    qs = analytics.compute_quantstats_metrics(list(returns_pct))

    def pct(key):
        value = qs.get(key)
        return None if value is None else value * _FRACTION_TO_PERCENT

    return {
        "total_return": pct("total_return"),
        "annualized_return": pct("annualized_return"),
        "max_drawdown": pct("max_drawdown"),
        "sharpe": qs.get("sharpe"),
        "sortino": qs.get("sortino"),
        "calmar": qs.get("calmar"),
    }


def _levels_total_return_and_drawdown(levels: Sequence[float]) -> tuple[float, float]:
    """Percent total return and max drawdown of a level series (first level is a peak)."""
    peak = levels[0]
    worst = 0.0
    for level in levels:
        peak = max(peak, level)
        worst = min(worst, level / peak - 1.0)
    return (
        (levels[-1] / levels[0] - 1.0) * _FRACTION_TO_PERCENT,
        worst * _FRACTION_TO_PERCENT,
    )


def _window_dict(first, last, days) -> dict:
    return {"first": first, "last": last, "days": days}


# ---------------------------------------------------------------------------
# Item construction
# ---------------------------------------------------------------------------


def _figure(value, unit: str, reason: str | None = None) -> dict:
    number = _finite(value)
    if number is None:
        return {"value": None, "unit": unit, "reason": reason or "not computable"}
    return {"value": number, "unit": unit}


def _item(
    status: str,
    reason: str | None,
    label: str,
    subject: str,
    source: str,
    window: dict | None = None,
    figures: dict | None = None,
    **extra,
) -> dict:
    return {
        "status": status,
        "reason": reason,
        "label": label,
        "window": window,
        "subject": subject,
        "source": source,
        "figures": figures if figures is not None else {},
        **extra,
    }


def _unavailable(
    specs, reason, label, subject, source, *, status="unavailable", window=None, **extra
) -> dict:
    figures = {name: _figure(None, unit, reason) for name, unit in specs}
    return _item(status, reason, label, subject, source, window, figures, **extra)


# ---------------------------------------------------------------------------
# Redundancy
# ---------------------------------------------------------------------------

_STRUCTURAL_NEEDS_DAYS = next(
    n
    for n in itertools.count(1)
    if -(-n * _STRESS_PERCENT // 100) >= retirement_recommender.STRESS_MIN_OBS
)


def _gate(state: str, text: str) -> dict:
    return {"state": state, "text": text}


def _evaluate_pair(a: str, b: str, series: Mapping, tickers: Mapping | None) -> dict:
    vals_a, vals_b, win = _extract_aligned_pairs(dict(series[a]), dict(series[b]))
    n_obs = len(vals_a)
    r = _pearson_r(vals_a, vals_b)
    screen_hit = r is not None and r >= retirement_recommender.CORRELATION_SCREEN_THRESHOLD
    pair_result = PairResult(
        sym_a=a, sym_b=b, n_obs=n_obs, correlation=r, thin_data=False, window=win
    )

    ci_lower = ci_upper = None
    if n_obs < retirement_recommender.MIN_OBS_FLOOR:
        uncertainty = _gate(
            "insufficient data",
            f"insufficient data: needs {retirement_recommender.MIN_OBS_FLOOR} aligned days",
        )
    elif r is None or abs(r) >= 1.0:
        uncertainty = _gate("undefined", "undefined: correlation interval cannot be computed")
    else:
        verdict = retirement_recommender.evaluate_uncertainty_gate(pair_result)
        ci_lower, ci_upper = verdict.ci_lower, verdict.ci_upper
        uncertainty = (
            _gate("passed", "passed")
            if verdict.passed
            else _gate("not met", "not met: interval lower bound below the screen threshold")
        )

    stressed = None
    if uncertainty["state"] != "passed":
        structural = _gate("not evaluated", "not evaluated: uncertainty gate not met")
    elif -(-n_obs * _STRESS_PERCENT // 100) < retirement_recommender.STRESS_MIN_OBS:
        structural = _gate(
            "insufficient data", f"insufficient data: needs {_STRUCTURAL_NEEDS_DAYS} aligned days"
        )
    else:
        stressed = retirement_recommender._compute_stressed_correlation(vals_a, vals_b)
        if stressed is None:
            structural = _gate(
                "undefined", "undefined: stressed-window correlation cannot be computed"
            )
        elif stressed >= retirement_recommender.STRESS_REDUNDANCY_THRESHOLD:
            structural = _gate("passed", "passed")
        else:
            structural = _gate("not met", "not met: stressed correlation below the threshold")

    tickers = tickers or {}
    overlap = retirement_recommender._compute_holdings_overlap(
        tickers.get(a) or {}, tickers.get(b) or {}
    )
    return {
        "sym_a": a,
        "sym_b": b,
        "correlation": r,
        "n_obs": n_obs,
        "window": _window_dict(win[0], win[1], n_obs) if win else None,
        "screen_hit": bool(screen_hit),
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "uncertainty_gate": uncertainty,
        "structural_gate": structural,
        "stressed_correlation": stressed,
        "holdings_overlap": overlap,
    }


def evaluate_redundancy(
    series_by_symphony: Mapping, current_tickers_by_symphony=None
) -> list[dict]:
    """One result per unordered pair of symphonies (see the handoff for the keys)."""
    ids = sorted(series_by_symphony)
    return [
        _evaluate_pair(a, b, series_by_symphony, current_tickers_by_symphony)
        for a, b in itertools.combinations(ids, 2)
    ]


def _passes_both_gates(pair: dict) -> bool:
    return (
        pair["uncertainty_gate"]["state"] == "passed"
        and pair["structural_gate"]["state"] == "passed"
    )


# ---------------------------------------------------------------------------
# Backtest item
# ---------------------------------------------------------------------------

_HTTP_CODE = re.compile(r"^HTTP (\d{3})")


def _backtest_error_reason(error: str) -> str:
    text = str(error).strip()
    match = _HTTP_CODE.match(text)
    if match:
        code = match.group(1)
        if code == "413":
            return "definition too large for Composer backtest (HTTP 413)"
        if code in ("400", "422"):
            return f"Composer rejected the backtest (HTTP {code})"
        if code == "429":
            return "Composer rate limit"
        if code.startswith("5"):
            return "Composer unavailable"
    lowered = text.lower()
    if lowered.startswith(("request timed out", "transport error")):
        return "Composer unavailable"
    if lowered.startswith("invalid json in 200 response"):
        return "Composer response unreadable"
    return "backtest unavailable"


def backtest_comparison(
    *,
    window: dict | None,
    realized_total_return,
    realized_max_drawdown,
    backtest: BacktestResult | None,
    definition_available: bool = True,
) -> dict:
    subject = "today's definition against the realized history"
    source = "Composer backtest (POST /api/v0.1/backtest)"

    def unavailable(reason):
        return _unavailable(
            _BACKTEST_FIGURES, reason, BACKTEST_LABEL, subject, source, window=window
        )

    if not definition_available:
        return unavailable("definition unavailable")
    if backtest is None:
        return unavailable("backtest not run")
    if backtest.error:
        return unavailable(_backtest_error_reason(backtest.error))
    if (
        window is None
        or backtest.first_day != window.get("first")
        or backtest.last_market_day != window.get("last")
    ):
        return unavailable("backtest dates do not match the realized window")
    levels = [
        level
        for key, raw in sorted(backtest.daily_portfolio_values.items())
        if window["first"] <= key <= window["last"]
        for level in [_usable_level(raw)]
        if level is not None
    ]
    if len(levels) < 2:
        return unavailable("backtest series too short")

    bt_total, bt_dd = _levels_total_return_and_drawdown(levels)
    realized_total = _finite(realized_total_return)
    realized_dd = _finite(realized_max_drawdown)
    gap_reason = "realized figure unavailable"
    figures = {
        "realized_total_return": _figure(realized_total, "percent", gap_reason),
        "realized_max_drawdown": _figure(realized_dd, "percent", gap_reason),
        "backtest_total_return": _figure(bt_total, "percent"),
        "backtest_max_drawdown": _figure(bt_dd, "percent"),
        "total_return_gap": _figure(
            None if realized_total is None else realized_total - bt_total,
            "percentage points",
            gap_reason,
        ),
        "max_drawdown_gap": _figure(
            None if realized_dd is None else realized_dd - bt_dd, "percentage points", gap_reason
        ),
    }
    return _item("available", None, BACKTEST_LABEL, subject, source, dict(window), figures)


# ---------------------------------------------------------------------------
# Marginal contribution
# ---------------------------------------------------------------------------


def marginal_contribution(subject_id: str, eligible: Mapping, excluded: Sequence[str] = ()) -> dict:
    subject = "portfolio of eligible symphonies with and without this symphony"
    excluded_list = [
        {"display_name": name, "reason": "excluded: not yet eligible"} for name in excluded
    ]
    me = eligible.get(subject_id) or {}
    basis = me.get("basis")
    label = MARGINAL_LABEL
    source = _COMPOSER_SOURCE

    def not_available(reason, status="unavailable"):
        return _unavailable(
            _MARGINAL_FIGURES,
            reason,
            label,
            subject,
            source,
            status=status,
            excluded=excluded_list,
        )

    if basis == "shadow":
        return not_available("no position values on the shadow-history basis")
    others = [k for k in eligible if k != subject_id]
    if any(eligible[k].get("basis") != basis for k in others):
        return not_available("bases differ")
    if not others:
        return not_available("no other eligible symphonies", status="not_applicable")

    usable = {
        k: {
            d: level
            for d, raw in (eligible[k].get("values") or {}).items()
            if (level := _usable_level(raw)) is not None
        }
        for k in eligible
    }
    shared = sorted(set.intersection(*(set(v) for v in usable.values())))
    if len(shared) < 2:
        return not_available("fewer than two shared days")
    with_levels = [sum(usable[k][d] for k in eligible) for d in shared]
    without_levels = [with_levels[i] - usable[subject_id][d] for i, d in enumerate(shared)]
    with_total, with_dd = _levels_total_return_and_drawdown(with_levels)
    without_total, without_dd = _levels_total_return_and_drawdown(without_levels)
    figures = {
        "with_total_return": _figure(with_total, "percent"),
        "with_max_drawdown": _figure(with_dd, "percent"),
        "without_total_return": _figure(without_total, "percent"),
        "without_max_drawdown": _figure(without_dd, "percent"),
        "total_return_difference": _figure(without_total - with_total, "percentage points"),
        "max_drawdown_difference": _figure(without_dd - with_dd, "percentage points"),
    }
    return _item(
        "available",
        None,
        label,
        subject,
        source,
        _window_dict(shared[0], shared[-1], len(shared)),
        figures,
        excluded=excluded_list,
    )


# ---------------------------------------------------------------------------
# Long-VIX co-firing
# ---------------------------------------------------------------------------


def _normalize_ticker(raw: str) -> str:
    return str(raw).split("::")[-1].split("//")[0].strip().upper()


def _long_vix_value(day_holdings: Mapping) -> tuple[float, float, bool]:
    """(long-VIX value, total value, holds a long-VIX ticker) for one symphony-day."""
    long_value = total = 0.0
    held = False
    for ticker, value in day_holdings.items():
        amount = _finite(value) or 0.0
        total += amount
        if _normalize_ticker(ticker) in LONG_VIX_TICKERS:
            long_value += amount
            held = True
    return long_value, total, held


def long_vix_cofiring(holdings, subject_id: str, live_ids, window: dict) -> dict:
    subject = "long-volatility (VIX) exposure beside the rest of the portfolio"
    source = "Composer account holdings by day"

    def unavailable(reason):
        return _unavailable(_LONG_VIX_FIGURES, reason, LONG_VIX_LABEL, subject, source)

    if holdings is None:
        return unavailable("holdings unavailable")
    if not holdings.days and holdings.failure:
        return unavailable(_FAILURE_REASONS.get(holdings.failure, "Composer unavailable"))
    live = set(live_ids)
    first, last = window.get("first"), window.get("last")
    days = [
        d
        for d in sorted(holdings.days)
        if first is not None
        and last is not None
        and first <= d <= last
        and subject_id in holdings.days[d]
    ]
    if not days:
        return unavailable("no holdings for this symphony in the window")

    held_days = co_held = 0
    peak_with = peak_without = None
    for d in days:
        by_symphony = {k: v for k, v in holdings.days[d].items() if k in live}
        long_with = total_with = long_without = total_without = 0.0
        subject_held = other_held = False
        for sym_id, day_holdings in by_symphony.items():
            long_value, total, held = _long_vix_value(day_holdings)
            long_with += long_value
            total_with += total
            if sym_id == subject_id:
                subject_held = held
            else:
                long_without += long_value
                total_without += total
                other_held = other_held or held
        if subject_held:
            held_days += 1
            co_held += int(other_held)
        if total_with > 0:
            share = long_with / total_with * _FRACTION_TO_PERCENT
            peak_with = share if peak_with is None else max(peak_with, share)
        if total_without > 0:
            share = long_without / total_without * _FRACTION_TO_PERCENT
            peak_without = share if peak_without is None else max(peak_without, share)

    figures = {
        "held_days": _figure(held_days, "days"),
        "co_held_days": _figure(co_held, "days"),
        "peak_share_with": _figure(peak_with, "percent", "no portfolio holdings on these days"),
        "peak_share_without": _figure(
            peak_without, "percent", "no other symphony holdings on these days"
        ),
    }
    return _item(
        "available",
        None,
        LONG_VIX_LABEL,
        subject,
        source,
        _window_dict(first, last, len(days)),
        figures,
    )


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

_TRAILS_REASON = "trails its own backtest"
_DRAGS_REASON = "drags portfolio return and drawdown"


def _figure_value(item: Mapping, name: str):
    return ((item.get("figures") or {}).get(name) or {}).get("value")


def _available(evidence: Mapping, name: str) -> Mapping | None:
    item = evidence.get(name)
    return item if item is not None and item.get("status") == "available" else None


def decide_verdict(evidence: Mapping) -> dict:
    missing = [
        name
        for name in EVIDENCE_ITEM_ORDER
        if name in evidence and evidence[name].get("status") == "unavailable"
    ]
    redundancy = _available(evidence, "redundancy")
    backtest = _available(evidence, "backtest")
    marginal = _available(evidence, "marginal")

    if redundancy is None and backtest is None and marginal is None:
        return {
            "verdict": None,
            "verdict_text": _NO_EVIDENCE_TEXT,
            "verdict_reasons": [],
            "missing_items": missing,
        }
    if redundancy is not None and redundancy.get("retire_candidate"):
        return {
            "verdict": "retire_candidate",
            "verdict_text": "retire-candidate",
            "verdict_reasons": [],
            "missing_items": missing,
        }

    reasons: list[str] = []
    if backtest is not None:
        backtest_total = _figure_value(backtest, "backtest_total_return")
        realized_total = _figure_value(backtest, "realized_total_return")
        if (
            backtest_total is not None
            and realized_total is not None
            and backtest_total - realized_total > BACKTEST_TRAIL_WATCH_PP
        ):
            reasons.append(_TRAILS_REASON)
    if marginal is not None:
        with_total = _figure_value(marginal, "with_total_return")
        without_total = _figure_value(marginal, "without_total_return")
        with_dd = _figure_value(marginal, "with_max_drawdown")
        without_dd = _figure_value(marginal, "without_max_drawdown")
        if (
            None not in (with_total, without_total, with_dd, without_dd)
            and without_total > with_total
            and without_dd >= with_dd
        ):
            reasons.append(_DRAGS_REASON)
    verdict = "watch" if reasons else "keep"
    return {
        "verdict": verdict,
        "verdict_text": verdict,
        "verdict_reasons": reasons,
        "missing_items": missing,
    }


# ---------------------------------------------------------------------------
# Review assembly
# ---------------------------------------------------------------------------


@dataclass
class _Prep:
    sym: SymphonyInput
    start: date
    tenure: int | None
    eligible: bool
    kind: str  # composer | shadow | none
    fallback_reason: str | None
    window: dict
    series: dict[str, float]
    values: dict[str, float]

    @property
    def label(self) -> str:
        return {"composer": COMPOSER_BASIS_LABEL, "shadow": SHADOW_BASIS_LABEL}.get(
            self.kind, _NO_BASIS_LABEL
        )

    @property
    def source(self) -> str:
        return {"composer": _COMPOSER_SOURCE, "shadow": _SHADOW_SOURCE}.get(
            self.kind, "no history available"
        )


def _prepare(sym: SymphonyInput, as_of: date, threshold: int) -> _Prep:
    start_day = _parse_date(sym.invested_since) if sym.invested_since else None
    if start_day is None:
        tenure = None
    elif start_day > as_of:
        tenure = 0
    else:
        tenure = count_trading_days(start_day, as_of)
    start = start_day or date.min

    kind = "none"
    fallback = None
    window: dict = {"first": None, "last": None, "days": 0, "dates": [], "returns_pct": []}
    series: dict[str, float] = {}
    values: dict[str, float] = {}

    if sym.composer_values and sym.series_failure is None:
        kept = realized_window(sym.composer_values, start, as_of)
        if kept["days"] >= 2:
            kind, window = "composer", kept
            values = {
                d: float(sym.composer_values[d]) for d in kept["dates"] if d in sym.composer_values
            }
            series = dict(zip(kept["dates"][1:], kept["returns_pct"], strict=True))
    if kind == "none":
        fallback = (
            _FAILURE_REASONS.get(sym.series_failure, _EMPTY_SERIES_REASON)
            if sym.series_failure
            else _EMPTY_SERIES_REASON
        )
        rows = {}
        for key, raw in (sym.shadow_returns or {}).items():
            d, ret = _parse_date(key), _finite(raw)
            if d is not None and ret is not None and start <= d <= as_of:
                rows[d.isoformat()] = ret
        if rows:
            ordered = sorted(rows)
            kind = "shadow"
            series = {d: rows[d] for d in ordered}
            window = {
                "first": ordered[0],
                "last": ordered[-1],
                "days": len(ordered),
                "dates": ordered,
                "returns_pct": [rows[d] for d in ordered],
            }
    eligible = tenure is not None and tenure >= threshold
    return _Prep(sym, start, tenure, eligible, kind, fallback, window, series, values)


def _performance_item(p: _Prep) -> tuple[dict, dict]:
    if not p.eligible:
        return _unavailable(
            _PERF_FIGURES, REASON_NOT_YET_ELIGIBLE, p.label, p.sym.display_name, p.source
        ), {}
    if p.kind == "none":
        return _unavailable(
            _PERF_FIGURES, REASON_NO_HISTORY, p.label, p.sym.display_name, p.source
        ), {}
    metrics = performance_metrics(p.window["returns_pct"])
    if all(v is None for v in metrics.values()):
        reason = "not computable: fewer than two finite returns"
        return _unavailable(_PERF_FIGURES, reason, p.label, p.sym.display_name, p.source), {}
    window = _window_dict(p.window["first"], p.window["last"], p.window["days"])
    figures = {
        name: _figure(metrics[name], unit, "undefined for this series")
        for name, unit in _PERF_FIGURES
    }
    return _item("available", None, p.label, p.sym.display_name, p.source, window, figures), metrics


def _composite_item(p: _Prep, reason: str | None, composite: float | None, window: dict) -> dict:
    if reason is not None or composite is None:
        return _unavailable(
            _COMPOSITE_FIGURES, reason or "not computable", p.label, p.sym.display_name, p.source
        )
    return _item(
        "available",
        None,
        p.label,
        p.sym.display_name,
        p.source,
        dict(window),
        {"composite": _figure(composite, _COMPOSITE_FIGURES[0][1])},
    )


def _note_for_sibling(sibling_reason: str | None) -> str | None:
    if sibling_reason is None:
        return None
    if sibling_reason == "not comparable: basis differs":
        return "sibling has no composite: basis differs"
    return f"sibling has no composite: {sibling_reason}"


def _pair_figures(pair: dict, sibling: _Prep, note: str | None) -> dict:
    return {
        "sibling_id": sibling.sym.symphony_id,
        "sibling_name": sibling.sym.display_name,
        "correlation": _figure(pair["correlation"], "correlation", "correlation is undefined"),
        "ci_lower": _figure(pair["ci_lower"], "correlation", "interval not computed"),
        "ci_upper": _figure(pair["ci_upper"], "correlation", "interval not computed"),
        "stressed_correlation": _figure(
            pair["stressed_correlation"], "correlation", "stressed-window correlation not computed"
        ),
        "holdings_overlap": _figure(pair["holdings_overlap"], "ratio", "holdings unavailable"),
        "n_obs": pair["n_obs"],
        "uncertainty_gate": pair["uncertainty_gate"],
        "structural_gate": pair["structural_gate"],
        "note": note,
    }


def build_review(inputs: ReviewInputs) -> ReviewResult:
    threshold = inputs.threshold.value
    as_of = inputs.as_of
    sessions = _last_sessions(as_of, threshold)
    composite_window = _window_dict(
        sessions[0].isoformat() if sessions else None, as_of.isoformat(), threshold
    )
    session_keys = [d.isoformat() for d in sessions]

    preps = [_prepare(s, as_of, threshold) for s in inputs.symphonies]
    by_id = {p.sym.symphony_id: p for p in preps}
    live_ids = [p.sym.symphony_id for p in preps]

    # Composite population: eligible, Composer basis, a usable point on every session.
    composite_reason: dict[str, str | None] = {}
    metrics_by_id: dict[str, dict] = {}
    for p in preps:
        sid = p.sym.symphony_id
        if not p.eligible:
            composite_reason[sid] = REASON_NOT_YET_ELIGIBLE
        elif p.kind == "none":
            composite_reason[sid] = REASON_NO_HISTORY
        elif p.kind != "composer":
            composite_reason[sid] = "not comparable: basis differs"
        else:
            covered = [k for k in session_keys if k in p.values]
            if len(covered) < len(session_keys):
                composite_reason[sid] = (
                    f"insufficient coverage in the common window ({len(covered)} of "
                    f"{len(session_keys)} days)"
                )
                continue
            returns = realized_window({k: p.values[k] for k in covered}, sessions[0], sessions[-1])[
                "returns_pct"
            ]
            metrics = performance_metrics(returns)
            metrics_by_id[sid] = metrics
            composite_reason[sid] = None
    scores = retirement_recommender.compute_composite_scores(metrics_by_id)
    for sid, score in scores.items():
        if not score.eligible:
            composite_reason[sid] = "not computable: a composite metric is undefined"

    # Redundancy pairs, computed within one basis only.
    tickers = {p.sym.symphony_id: p.sym.current_tickers for p in preps if p.sym.current_tickers}
    pairs: list[dict] = []
    for kind in ("composer", "shadow"):
        group = {p.sym.symphony_id: p.series for p in preps if p.kind == kind}
        pairs.extend(evaluate_redundancy(group, tickers))

    # Marginal inputs.
    eligible_inputs = {
        p.sym.symphony_id: {"basis": p.kind, "values": p.values if p.kind == "composer" else None}
        for p in preps
        if p.eligible
    }
    excluded_names = [p.sym.display_name for p in preps if not p.eligible]

    entries = []
    for p in preps:
        entries.append(
            _build_entry(
                p,
                preps=preps,
                by_id=by_id,
                pairs=pairs,
                scores=scores,
                composite_reason=composite_reason,
                composite_window=composite_window,
                eligible_inputs=eligible_inputs,
                excluded_names=excluded_names,
                holdings=inputs.holdings,
                live_ids=live_ids,
                threshold=threshold,
            )
        )
    return ReviewResult(
        run_id=inputs.run_id,
        trigger=inputs.trigger,
        started_at=inputs.started_at,
        as_of=as_of,
        threshold=inputs.threshold,
        composite_window=composite_window,
        entries=entries,
    )


def _build_entry(
    p: _Prep,
    *,
    preps,
    by_id,
    pairs,
    scores,
    composite_reason,
    composite_window,
    eligible_inputs,
    excluded_names,
    holdings,
    live_ids,
    threshold,
) -> dict:
    sym = p.sym
    sid = sym.symphony_id
    name = sym.display_name

    if p.tenure is None:
        days_remaining = None
        text = "tenure unavailable: Composer did not report an invested-since date"
    elif p.tenure < threshold:
        days_remaining = threshold - p.tenure
        unit = "trading day" if days_remaining == 1 else "trading days"
        text = f"not yet eligible: {days_remaining} {unit} remaining"
    else:
        days_remaining = None
        text = "eligible"

    basis = {
        "kind": p.kind,
        "label": p.label,
        "fallback_reason": p.fallback_reason if p.kind != "composer" else None,
        "series_begins": p.window["first"] if p.kind != "none" else None,
        "tenure_begins": sym.invested_since,
    }
    entry = {
        "symphony_id": sid,
        "display_name": name,
        "invested_since": sym.invested_since,
        "tenure_trading_days": p.tenure,
        "eligibility_text": text,
        "eligible": p.eligible,
        "days_remaining": days_remaining,
        "basis": basis,
    }

    if not p.eligible or p.kind == "none":
        reason = REASON_NOT_YET_ELIGIBLE if not p.eligible else REASON_NO_HISTORY
        evidence = {
            "performance": _unavailable(_PERF_FIGURES, reason, p.label, name, p.source),
            "composite": _unavailable(_COMPOSITE_FIGURES, reason, p.label, name, p.source),
            "redundancy": _redundancy_unavailable(p, reason),
            "backtest": _unavailable(_BACKTEST_FIGURES, reason, BACKTEST_LABEL, name, p.source),
            "marginal": _unavailable(
                _MARGINAL_FIGURES,
                reason,
                MARGINAL_LABEL,
                name,
                p.source,
                excluded=[
                    {"display_name": n, "reason": "excluded: not yet eligible"}
                    for n in excluded_names
                ],
            ),
            "long_vix": _unavailable(_LONG_VIX_FIGURES, reason, LONG_VIX_LABEL, name, p.source),
        }
        if not p.eligible:
            entry.update(
                verdict=None,
                verdict_text="no verdict: tenure unavailable"
                if p.tenure is None
                else "no verdict: not yet eligible",
                verdict_reasons=[],
                missing_items=[],
            )
        else:
            entry.update(decide_verdict(evidence))
        entry["evidence"] = evidence
        return entry

    performance, metrics = _performance_item(p)
    score = scores.get(sid)
    composite = _composite_item(
        p,
        composite_reason.get(sid),
        score.composite if score is not None else None,
        composite_window,
    )
    redundancy = _redundancy_item(p, preps, by_id, pairs, scores, composite_reason)

    if performance["status"] == "available":
        backtest = backtest_comparison(
            window=performance["window"],
            realized_total_return=metrics.get("total_return"),
            realized_max_drawdown=metrics.get("max_drawdown"),
            backtest=sym.backtest,
            definition_available=sym.definition_available,
        )
    else:
        backtest = _unavailable(
            _BACKTEST_FIGURES, performance["reason"], BACKTEST_LABEL, name, p.source
        )
    marginal = marginal_contribution(sid, eligible_inputs, excluded_names)
    marginal["subject"] = f"{name}: eligible portfolio with and without this symphony"
    marginal["label"] = MARGINAL_LABEL
    long_vix = long_vix_cofiring(
        holdings,
        sid,
        live_ids,
        {"first": p.window["first"], "last": p.window["last"]},
    )
    evidence = {
        "performance": performance,
        "composite": composite,
        "redundancy": redundancy,
        "backtest": backtest,
        "marginal": marginal,
        "long_vix": long_vix,
    }
    entry.update(decide_verdict(evidence))
    entry["evidence"] = evidence
    return entry


def _redundancy_unavailable(p: _Prep, reason: str) -> dict:
    return _item(
        "unavailable",
        reason,
        p.label,
        p.sym.display_name,
        "pairwise return correlation across live symphonies",
        None,
        {},
        retire_candidate=False,
        pair=None,
    )


def _redundancy_item(p: _Prep, preps, by_id, pairs, scores, composite_reason) -> dict:
    sid = p.sym.symphony_id
    source = "pairwise return correlation across live symphonies"
    mine = [x for x in pairs if sid in (x["sym_a"], x["sym_b"])]
    if not mine:
        others = [
            q
            for q in preps
            if q is not p and q.kind != "none" and q.eligible or (q is not p and q.kind != "none")
        ]
        if any(q.kind != p.kind for q in others):
            return _redundancy_unavailable(p, "bases differ")
        return _item(
            "not_applicable",
            "no other symphonies to compare",
            p.label,
            p.sym.display_name,
            source,
            None,
            {},
            retire_candidate=False,
            pair=None,
        )

    def sibling_of(pair):
        return pair["sym_b"] if pair["sym_a"] == sid else pair["sym_a"]

    flagged = [
        x
        for x in mine
        if _passes_both_gates(x)
        and retirement_recommender.select_retirement_candidate(x["sym_a"], x["sym_b"], scores)
        == sid
    ]
    hits = [x for x in mine if x["screen_hit"]]
    if flagged:
        shown = max(flagged, key=lambda x: x["correlation"])
    elif hits:
        shown = max(hits, key=lambda x: x["correlation"])
    else:
        shown = None

    pair = None
    window = None
    if shown is not None:
        sibling = by_id[sibling_of(shown)]
        sib_reason = composite_reason.get(sibling.sym.symphony_id)
        pair = _pair_figures(shown, sibling, _note_for_sibling(sib_reason))
        window = shown["window"]
    return _item(
        "available",
        None,
        p.label,
        p.sym.display_name,
        source,
        window,
        {},
        retire_candidate=bool(flagged),
        pair=pair,
    )


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------


def _clean(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def run_record(result: ReviewResult, completed_at: datetime) -> dict:
    """The persisted record of one review run: strict JSON, no non-finite numbers."""
    return _clean(
        {
            "run_id": result.run_id,
            "trigger": result.trigger,
            "started_at": result.started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "as_of_trading_day": result.as_of.isoformat(),
            "threshold_in_effect": result.threshold.value,
            "threshold_note": result.threshold.note,
            "composite_window": result.composite_window,
            "entries": result.entries,
        }
    )


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


class _ReviewRunState:
    """Process-wide single-flight guard for review runs (module level mutable state)."""

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._running = False
        self._running_since: str | None = None
        self._trigger: str | None = None
        self._last_failed: dict | None = None

    def try_start(self, trigger: str) -> bool:
        with self._mutex:
            if self._running:
                return False
            self._running = True
            self._running_since = datetime.now(UTC).isoformat()
            self._trigger = trigger
            return True

    def _free(self) -> None:
        self._running = False
        self._running_since = None
        self._trigger = None

    def finish_success(self) -> None:
        with self._mutex:
            self._free()
            self._last_failed = None

    def finish_failure(self, reason: str, at) -> None:
        with self._mutex:
            self._free()
            self._last_failed = {
                "at": at.isoformat() if isinstance(at, datetime) else str(at),
                "reason": reason,
            }

    def release(self) -> None:
        with self._mutex:
            self._free()

    def snapshot(self) -> dict:
        with self._mutex:
            return {
                "running": self._running,
                "running_since": self._running_since,
                "trigger": self._trigger,
                "last_failed_attempt": dict(self._last_failed) if self._last_failed else None,
            }


REVIEW_RUN_STATE = _ReviewRunState()
