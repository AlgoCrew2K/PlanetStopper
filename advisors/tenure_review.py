"""Symphony tenure review: advisory compute.

For every live symphony this module assembles six labelled evidence items
(performance, composite, redundancy, backtest, marginal, long-VIX co-firing) and
a verdict (retire-candidate, watch, keep or no verdict). It is pure compute: no
network, no database write, no Composer client and no order, invest, deploy or
liquidation path. Callers fetch the inputs and persist the run record.

Percent scale everywhere: a return of 0.5 means +0.5 percent. Max drawdown is a
percent number that is <= 0. ``None`` means unknown or not computable and is never
replaced by 0; a computed 0 stays 0. The one exception to percent scale is the
metrics inside a retire-candidate recommendation, which keep the scale of
``analytics.compute_quantstats_metrics`` (fractions) so that rows written by the
existing recommender and rows written from a review share one meaning per key.
"""

from __future__ import annotations

import itertools
import math
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import analytics
import market_calendar
from advisors import frontrunner_detector, retirement_recommender
from advisors.correlation_diagnostic import PairResult, _extract_aligned_pairs, _pearson_r

if TYPE_CHECKING:
    from advisors.composer_backtest_client import BacktestResult

# Default minimum tenure, in NYSE trading days, before a symphony gets a verdict
# (about six months of sessions: 126 = 252 / 2, half of a 252-session trading year).
DEFAULT_MIN_TENURE_TRADING_DAYS: int = 126

# Environment key an operator sets to override the default tenure threshold.
TENURE_THRESHOLD_ENV_KEY: str = "TENURE_REVIEW_MIN_TRADING_DAYS"

# A symphony whose backtest of today's definition beats its realized total return
# by more than this many percentage points is flagged "watch" (operator decision;
# unit: percentage points of total return).
BACKTEST_TRAIL_WATCH_PP: float = 10.0

# Long-volatility VIX products: the frontrunner detector's VIX family without the
# short products SVXY and SVIX (inverse products profit when volatility falls).
LONG_VIX_TICKERS: frozenset[str] = frontrunner_detector.VIX_FAMILY_TICKERS - {"SVXY", "SVIX"}

# advisor_role of the persisted run record row.
TENURE_REVIEW_RUN_ROLE: str = "TENURE_REVIEW_RUN"

# Multiplier that converts a fraction to percent (0.01 is 1 percent) and the integer
# denominator of a share given in whole percent.
_FRACTION_TO_PERCENT: int = 100

# Earliest invested-since date the calendar walk supports; an earlier date, or text
# that is not a date, leaves the tenure unknown instead of walking centuries of days.
CALENDAR_FLOOR: date = date(1990, 1, 1)

# Exchange time zone in which a session closes and the as-of day is read.
_ET = ZoneInfo("America/New_York")

# Note shown beside the threshold when the configured value cannot be used; derived
# from the default so the two never disagree.
INVALID_THRESHOLD_NOTE = f"configured threshold invalid; {DEFAULT_MIN_TENURE_TRADING_DAYS} in use"

# Characters of an ISO timestamp that name its calendar date (YYYY-MM-DD).
_ISO_DATE_LENGTH: int = 10

# Fewest usable points a Composer series needs to serve as the realized basis.
_MIN_COMPOSER_POINTS: int = 2

# Fewest values the shared metric function can score: it needs a minimum number of
# returns, and n returns need n + 1 values.
_MIN_VALUES_TO_SCORE: int = analytics._MIN_QUANTSTATS_OBSERVATIONS + 1

# Label of the basis built from Composer's deposit-adjusted account history.
COMPOSER_BASIS_LABEL = "realized: Composer account, deposit-adjusted"

# Label of the basis built from PlanetStopper's own shadow history.
SHADOW_BASIS_LABEL = "PlanetStopper shadow history from the 2026-06-22 go-live floor"

# Label of the counterfactual backtest item.
BACKTEST_LABEL = "counterfactual: Composer backtest of today's definition"

# Label of the counterfactual portfolio-without-this-symphony item.
MARGINAL_LABEL = "counterfactual: portfolio without this symphony"

# Label of the long-VIX item, which never feeds the verdict.
LONG_VIX_LABEL = "informational: not used in the verdict"

# Source text of a figure built from Composer's account history.
_COMPOSER_SOURCE = "Composer account portfolio history (deposit-adjusted)"

# Source text of a figure built from the shadow history table.
_SHADOW_SOURCE = "PlanetStopper shadow_history"

# Source text of an item with no series behind it.
_NO_SOURCE = "no history available"

# Basis kind of a symphony measured on Composer's account history.
_KIND_COMPOSER = "composer"

# Basis kind of a symphony measured on PlanetStopper's shadow history.
_KIND_SHADOW = "shadow"

# Basis kind of a symphony with no series on either basis.
_KIND_NONE = "none"

# Reason for each series failure class, shared by the series fallback and the
# holdings read so that one class always reads one way.
_FAILURE_REASONS: dict[str, str] = {
    "refused": "Composer refused the request",
    "not_found": "Composer returned not found",
    "rate_limited": "Composer rate limit",
    "unavailable": "Composer unavailable",
    "empty": "Composer series empty",
    "unreadable": "Composer response unreadable",
    "rejected": "Composer rejected the request",
}

# Reason for a failure class this module does not know.
_UNRECOGNIZED_FAILURE_REASON = "Composer read failed"

# Reason when Composer answered with points but fewer than a basis needs in the tenure.
_SHORT_SERIES_REASON = (
    f"Composer series has fewer than {_MIN_COMPOSER_POINTS} usable points in the tenure"
)

# Share of aligned days forming the stressed window, as an integer percent so the
# day count is exact (a float product can land on the wrong side of an integer).
_STRESS_PERCENT: int = round(retirement_recommender.STRESS_WINDOW_FRACTION * _FRACTION_TO_PERCENT)

# Names of the six evidence items, in display order.
EVIDENCE_ITEM_ORDER: tuple[str, ...] = (
    "performance",
    "composite",
    "redundancy",
    "backtest",
    "marginal",
    "long_vix",
)

# Unit of a difference between two percent figures.
_POINTS_UNIT = "percentage points"

# Unit of a daily, non-annualized ratio such as Sharpe or Sortino.
_DAILY_RATIO_UNIT = "ratio (daily, non-annualized)"

# Unit of the fleet-normalized composite score.
_COMPOSITE_UNIT = "score (0 to 1, fleet-normalized)"

# Figures of the performance item and their units.
_PERF_FIGURES = (
    ("total_return", "percent"),
    ("annualized_return", "percent"),
    ("max_drawdown", "percent"),
    ("sharpe", _DAILY_RATIO_UNIT),
    ("sortino", _DAILY_RATIO_UNIT),
    ("calmar", "ratio"),
)

# Figure of the composite item and its unit.
_COMPOSITE_FIGURES = (("composite", _COMPOSITE_UNIT),)

# Figures of the backtest item and their units.
_BACKTEST_FIGURES = (
    ("realized_total_return", "percent"),
    ("realized_max_drawdown", "percent"),
    ("backtest_total_return", "percent"),
    ("backtest_max_drawdown", "percent"),
    ("total_return_gap", _POINTS_UNIT),
    ("max_drawdown_gap", _POINTS_UNIT),
)

# Figures of the marginal item and their units.
_MARGINAL_FIGURES = (
    ("with_total_return", "percent"),
    ("with_max_drawdown", "percent"),
    ("without_total_return", "percent"),
    ("without_max_drawdown", "percent"),
    ("total_return_difference", _POINTS_UNIT),
    ("max_drawdown_difference", _POINTS_UNIT),
)

# Figures of the long-VIX item and their units.
_LONG_VIX_FIGURES = (
    ("held_days", "days"),
    ("co_held_days", "days"),
    ("peak_share_with", "percent"),
    ("peak_share_without", "percent"),
)

# Reason an item is withheld for a symphony younger than the threshold.
REASON_NOT_YET_ELIGIBLE = "not yet eligible"

# Reason an item is withheld for a symphony with no series on any basis; it is also
# the basis label such a symphony shows.
REASON_NO_HISTORY = "no history"

# Reason an item is withheld for a symphony whose invested-since date is unknown.
REASON_TENURE_UNAVAILABLE = "tenure unavailable"

# Reason a figure has no value when nothing more specific applies.
_NOT_COMPUTABLE = "not computable"

# Verdict text when no evidence item is available.
_NO_EVIDENCE_TEXT = "no verdict: no evidence available"

# Prefix of the reason a symphony is left out of the marginal comparison.
_EXCLUDED_PREFIX = "excluded: "

# Text of a redundancy item that was evaluated and found no redundant sibling.
_NO_SIBLING_TEXT = "no redundant sibling"

# Gate state of a pair that met the gate.
_GATE_PASSED = "passed"

# Gate state of a pair that was evaluated and fell short.
_GATE_NOT_MET = "not met"

# Gate state of a pair with too few aligned days to evaluate.
_GATE_INSUFFICIENT = "insufficient data"

# Gate state of a pair whose statistic cannot be computed.
_GATE_UNDEFINED = "undefined"

# Gate state of a gate that was not reached because an earlier gate was not met.
_GATE_NOT_EVALUATED = "not evaluated"

# Composite reason of a symphony on the fallback basis.
_BASIS_DIFFERS_REASON = "not comparable: basis differs"

# Shorter note a sibling row shows for a sibling on the fallback basis.
_BASIS_DIFFERS_NOTE = "basis differs"

# Prefix of the note shown beside a sibling that has no composite.
_SIBLING_NOTE_PREFIX = "sibling has no composite: "

# Reason shown by items that compare symphonies across different bases.
_BASES_DIFFER_REASON = "bases differ"

# Reason of the marginal item on the shadow basis, which carries no position values.
_NO_POSITION_VALUES_REASON = "no position values on the shadow-history basis"

# Reason a figure of an item is missing because the realized side is unknown.
_REALIZED_UNAVAILABLE_REASON = "realized figure unavailable"

# What the performance item measures; the review prefixes the symphony's name.
_PERFORMANCE_SUBJECT = "realized performance over the tenure"

# What the composite item measures; the review prefixes the symphony's name.
_COMPOSITE_SUBJECT = "performance score ranked against the other live symphonies"

# What the redundancy item measures; the review prefixes the symphony's name.
_REDUNDANCY_SUBJECT = "return correlation with the other live symphonies"

# Where the redundancy item's figures come from.
_REDUNDANCY_SOURCE = "pairwise return correlation across live symphonies"

# What the backtest item measures; the review prefixes the symphony's name.
_BACKTEST_SUBJECT = "today's definition against the realized history"

# Where the backtest item's figures come from.
_BACKTEST_SOURCE = "Composer backtest (POST /api/v0.1/backtest)"

# What the marginal item measures; the review prefixes the symphony's name.
_MARGINAL_SUBJECT = "portfolio of eligible symphonies with and without this symphony"

# What the long-VIX item measures; the review prefixes the symphony's name.
_LONG_VIX_SUBJECT = "long-volatility (VIX) exposure beside the rest of the portfolio"

# Where the long-VIX item's figures come from.
_LONG_VIX_SOURCE = "Composer account holdings by day"

# Reason shown for a figure that needs account holdings the caller did not supply.
_HOLDINGS_UNAVAILABLE_REASON = "holdings unavailable"

# Reason shown for an interval bound that was not computed.
_INTERVAL_NOT_COMPUTED_REASON = "interval not computed"

# Matches the status code at the start of a backtest client error text.
_HTTP_CODE = re.compile(r"^HTTP (\d{3})")

# Verdict reason when the backtest of today's definition beats the realized return.
_TRAILS_REASON = "trails its own backtest"

# Verdict reason when the portfolio would have done better without the symphony.
_DRAGS_REASON = "drags portfolio return and drawdown"


# ---------------------------------------------------------------------------
# Input and result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdSetting:
    """The minimum tenure in effect and a note when the configured value was unusable."""

    value: int
    note: str | None


@dataclass
class SymphonyInput:
    """Everything the review needs to know about one live symphony.

    ``composer_values`` maps a date key to a deposit-adjusted portfolio value;
    ``series_failure`` names the failure class of the Composer read, if it failed;
    ``shadow_returns`` maps a date key to a percent return from the shadow history.
    """

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
    """Account holdings as ``{day: {symphony id: {ticker: market value}}}``."""

    days: Mapping[str, Mapping[str, Mapping[str, float]]]
    failure: str | None = None


@dataclass
class ReviewInputs:
    """One review run: its identity, the as-of session, the threshold and the symphonies."""

    run_id: str
    trigger: str
    started_at: datetime
    as_of: date
    threshold: ThresholdSetting
    symphonies: Sequence[SymphonyInput]
    holdings: HoldingsInput | None = None


@dataclass
class ReviewResult:
    """The entries of one review, the composite window and the retire-candidate recommendations."""

    run_id: str
    trigger: str
    started_at: datetime
    as_of: date
    threshold: ThresholdSetting
    composite_window: dict
    entries: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Threshold, calendar
# ---------------------------------------------------------------------------


def parse_threshold(raw: object) -> ThresholdSetting:
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


@cache
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
    while not _is_session(d) and d > CALENDAR_FLOOR:
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
    while len(out) < count and d > CALENDAR_FLOOR:
        if _is_session(d):
            out.append(d)
        d -= timedelta(days=1)
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Series and metrics
# ---------------------------------------------------------------------------


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _usable_level(value: object) -> float | None:
    f = _finite(value)
    return f if f is not None and f > 0 else None


def _parse_date(text: object) -> date | None:
    try:
        return date.fromisoformat(str(text)[:_ISO_DATE_LENGTH])
    except ValueError:
        return None


def _points_by_date(values: Mapping, start: date, end: date, usable) -> dict[date, float]:
    """Usable values in [start, end] by date. A key is read as the date in its first ten
    characters; among keys naming one date the greatest key with a usable value wins,
    whatever the order of the mapping."""
    points: dict[date, float] = {}
    for key in sorted(values, key=str):
        d = _parse_date(key)
        value = usable(values[key])
        if d is not None and value is not None and start <= d <= end:
            points[d] = value
    return points


def _percent_changes(levels: Sequence[float]) -> list[float]:
    return [
        (later / earlier - 1.0) * _FRACTION_TO_PERCENT
        for earlier, later in itertools.pairwise(levels)
    ]


def realized_window(values: Mapping, start: date, end: date) -> dict:
    """Usable points in [start, end] and the percent returns between consecutive ones."""
    points = _points_by_date(values, start, end, _usable_level)
    ordered = sorted(points)
    return {
        "first": ordered[0].isoformat() if ordered else None,
        "last": ordered[-1].isoformat() if ordered else None,
        "days": len(ordered),
        "dates": [d.isoformat() for d in ordered],
        "returns_pct": _percent_changes([points[d] for d in ordered]),
    }


def _scored_metrics(returns_pct: Sequence[float]) -> dict:
    """The shared metric function's answer (fractions), except that a window with no
    negative return has a max drawdown of exactly 0 and so no Calmar ratio: the function
    reads such a series as prices when a day is +100 percent or more."""
    metrics = analytics.compute_quantstats_metrics(list(returns_pct))
    if returns_pct and min(returns_pct) >= 0 and metrics.get("max_drawdown") is not None:
        return {**metrics, "max_drawdown": 0.0, "calmar": None}
    return metrics


def performance_metrics(returns_pct: Sequence[float]) -> dict:
    """The six performance figures from percent returns (see module docstring)."""
    qs = _scored_metrics(returns_pct)

    def pct(key: str) -> float | None:
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


def _difference(minuend: float | None, subtrahend: float | None) -> float | None:
    return None if minuend is None or subtrahend is None else minuend - subtrahend


def _window_dict(first: str | None, last: str | None, days: int) -> dict:
    return {"first": first, "last": last, "days": days}


# ---------------------------------------------------------------------------
# Item construction
# ---------------------------------------------------------------------------


def _figure(value: object, unit: str, reason: str | None = None) -> dict:
    number = _finite(value)
    if number is None:
        return {"value": None, "unit": unit, "reason": reason or _NOT_COMPUTABLE}
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


def _stressed_day_count(aligned_days: int) -> int:
    """Days in the stressed window: the share of aligned days, rounded up."""
    return -(-aligned_days * _STRESS_PERCENT // _FRACTION_TO_PERCENT)


# Aligned days at which the stressed window first reaches the recommender's minimum.
_STRUCTURAL_NEEDS_DAYS = next(
    n for n in itertools.count(1) if _stressed_day_count(n) >= retirement_recommender.STRESS_MIN_OBS
)


def _insufficient_text(days: int) -> str:
    return f"{_GATE_INSUFFICIENT}: needs {days} aligned days"


def _gate(state: str, text: str | None = None) -> dict:
    return {"state": state, "text": text or state}


def _evaluate_pair(a: str, b: str, series: Mapping, tickers: Mapping | None) -> dict:
    vals_a, vals_b, win = _extract_aligned_pairs(dict(series[a]), dict(series[b]))
    n_obs = len(vals_a)
    r = _pearson_r(vals_a, vals_b)
    screen_hit = r is not None and r >= retirement_recommender.CORRELATION_SCREEN_THRESHOLD
    pair_result = PairResult(
        sym_a=a, sym_b=b, n_obs=n_obs, correlation=r, thin_data=False, window=win
    )
    tickers = tickers or {}
    overlap = retirement_recommender._compute_holdings_overlap(
        tickers.get(a) or {}, tickers.get(b) or {}
    )

    ci_lower = ci_upper = None
    if n_obs < retirement_recommender.MIN_OBS_FLOOR:
        uncertainty = _gate(
            _GATE_INSUFFICIENT, _insufficient_text(retirement_recommender.MIN_OBS_FLOOR)
        )
    elif r is None or abs(r) >= 1.0:
        uncertainty = _gate(_GATE_UNDEFINED, "undefined: correlation interval cannot be computed")
    else:
        verdict = retirement_recommender.evaluate_uncertainty_gate(pair_result)
        ci_lower, ci_upper = verdict.ci_lower, verdict.ci_upper
        uncertainty = (
            _gate(_GATE_PASSED)
            if verdict.passed
            else _gate(_GATE_NOT_MET, "not met: interval lower bound below the screen threshold")
        )

    stressed = None
    if uncertainty["state"] != _GATE_PASSED:
        structural = _gate(_GATE_NOT_EVALUATED, "not evaluated: uncertainty gate not met")
    elif _stressed_day_count(n_obs) < retirement_recommender.STRESS_MIN_OBS:
        structural = _gate(_GATE_INSUFFICIENT, _insufficient_text(_STRUCTURAL_NEEDS_DAYS))
    else:
        stressed = retirement_recommender._compute_stressed_correlation(vals_a, vals_b)
        if stressed is None:
            structural = _gate(
                _GATE_UNDEFINED, "undefined: stressed-window correlation cannot be computed"
            )
        elif retirement_recommender.evaluate_structural_redundancy_gate(
            pair_result, stressed, overlap
        ).passed:
            structural = _gate(_GATE_PASSED)
        else:
            structural = _gate(_GATE_NOT_MET, "not met: stressed correlation below the threshold")

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
    series_by_symphony: Mapping, current_tickers_by_symphony: Mapping | None = None
) -> list[dict]:
    """One result per unordered pair of symphonies: correlation, interval, both gate
    states, the stressed-window correlation and the holdings overlap."""
    ids = sorted(series_by_symphony)
    return [
        _evaluate_pair(a, b, series_by_symphony, current_tickers_by_symphony)
        for a, b in itertools.combinations(ids, 2)
    ]


def _passes_both_gates(pair: dict) -> bool:
    return (
        pair["uncertainty_gate"]["state"] == _GATE_PASSED
        and pair["structural_gate"]["state"] == _GATE_PASSED
    )


# ---------------------------------------------------------------------------
# Backtest item
# ---------------------------------------------------------------------------


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
            return _FAILURE_REASONS["rate_limited"]
        if code.startswith("5"):
            return _FAILURE_REASONS["unavailable"]
    lowered = text.lower()
    if lowered.startswith(("request timed out", "transport error")):
        return _FAILURE_REASONS["unavailable"]
    if lowered.startswith("invalid json in 200 response"):
        return _FAILURE_REASONS["unreadable"]
    return "backtest unavailable"


def _backtest_unavailable(reason: str, window: dict | None = None) -> dict:
    return _unavailable(
        _BACKTEST_FIGURES,
        reason,
        BACKTEST_LABEL,
        _BACKTEST_SUBJECT,
        _BACKTEST_SOURCE,
        window=window,
    )


def backtest_comparison(
    *,
    window: dict | None,
    realized_total_return: float | None,
    realized_max_drawdown: float | None,
    backtest: BacktestResult | None,
    definition_available: bool = True,
) -> dict:
    """Realized total return and drawdown beside those of a Composer backtest of today's
    definition over the same dates, with the gaps (realized minus backtest)."""
    if not definition_available:
        return _backtest_unavailable("definition unavailable", window)
    if backtest is None:
        return _backtest_unavailable("backtest not run", window)
    if backtest.error:
        return _backtest_unavailable(_backtest_error_reason(backtest.error), window)
    first = _parse_date(window.get("first")) if window else None
    last = _parse_date(window.get("last")) if window else None
    if (
        first is None
        or last is None
        or backtest.first_day != window["first"]
        or backtest.last_market_day != window["last"]
    ):
        return _backtest_unavailable("backtest dates do not match the realized window", window)
    levels = _points_by_date(backtest.daily_portfolio_values, first, last, _usable_level)
    if len(levels) < _MIN_VALUES_TO_SCORE:
        return _backtest_unavailable(
            f"backtest series too short to score: needs at least {_MIN_VALUES_TO_SCORE} values",
            window,
        )

    leg = performance_metrics(_percent_changes([levels[d] for d in sorted(levels)]))
    bt_total, bt_dd = leg["total_return"], leg["max_drawdown"]
    realized_total = _finite(realized_total_return)
    realized_dd = _finite(realized_max_drawdown)
    figures = {
        "realized_total_return": _figure(realized_total, "percent", _REALIZED_UNAVAILABLE_REASON),
        "realized_max_drawdown": _figure(realized_dd, "percent", _REALIZED_UNAVAILABLE_REASON),
        "backtest_total_return": _figure(bt_total, "percent"),
        "backtest_max_drawdown": _figure(bt_dd, "percent"),
        "total_return_gap": _figure(
            _difference(realized_total, bt_total), _POINTS_UNIT, _REALIZED_UNAVAILABLE_REASON
        ),
        "max_drawdown_gap": _figure(
            _difference(realized_dd, bt_dd), _POINTS_UNIT, _REALIZED_UNAVAILABLE_REASON
        ),
    }
    return _item(
        "available",
        None,
        BACKTEST_LABEL,
        _BACKTEST_SUBJECT,
        _BACKTEST_SOURCE,
        dict(window),
        figures,
    )


# ---------------------------------------------------------------------------
# Marginal contribution
# ---------------------------------------------------------------------------


def _excluded_record(excluded: str | Mapping[str, str]) -> dict:
    """A plain name is a symphony left out for being too young; a mapping is a ready record."""
    if isinstance(excluded, str):
        return {
            "display_name": excluded,
            "reason": f"{_EXCLUDED_PREFIX}{REASON_NOT_YET_ELIGIBLE}",
        }
    return dict(excluded)


def marginal_contribution(
    subject_id: str, eligible: Mapping, excluded: Sequence[str | Mapping[str, str]] = ()
) -> dict:
    """The eligible symphonies' summed portfolio with and without ``subject_id``, over the
    days on which every eligible symphony has a usable value. ``excluded`` lists the
    symphonies left out of the comparison."""
    excluded_list = [_excluded_record(e) for e in excluded]
    basis = (eligible.get(subject_id) or {}).get("basis")

    def not_available(reason: str, status: str = "unavailable") -> dict:
        return _unavailable(
            _MARGINAL_FIGURES,
            reason,
            MARGINAL_LABEL,
            _MARGINAL_SUBJECT,
            _COMPOSER_SOURCE,
            status=status,
            excluded=excluded_list,
        )

    if basis == _KIND_SHADOW:
        return not_available(_NO_POSITION_VALUES_REASON)
    others = [k for k in eligible if k != subject_id]
    if any(eligible[k].get("basis") != basis for k in others):
        return not_available(_BASES_DIFFER_REASON)
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
    if len(shared) < _MIN_VALUES_TO_SCORE:
        return not_available(
            f"shared days too short to score: needs at least {_MIN_VALUES_TO_SCORE} days"
        )
    with_levels = [sum(usable[k][d] for k in eligible) for d in shared]
    without_levels = [with_levels[i] - usable[subject_id][d] for i, d in enumerate(shared)]
    with_leg = performance_metrics(_percent_changes(with_levels))
    without_leg = performance_metrics(_percent_changes(without_levels))
    figures = {
        "with_total_return": _figure(with_leg["total_return"], "percent"),
        "with_max_drawdown": _figure(with_leg["max_drawdown"], "percent"),
        "without_total_return": _figure(without_leg["total_return"], "percent"),
        "without_max_drawdown": _figure(without_leg["max_drawdown"], "percent"),
        "total_return_difference": _figure(
            _difference(without_leg["total_return"], with_leg["total_return"]), _POINTS_UNIT
        ),
        "max_drawdown_difference": _figure(
            _difference(without_leg["max_drawdown"], with_leg["max_drawdown"]), _POINTS_UNIT
        ),
    }
    return _item(
        "available",
        None,
        MARGINAL_LABEL,
        _MARGINAL_SUBJECT,
        _COMPOSER_SOURCE,
        _window_dict(shared[0], shared[-1], len(shared)),
        figures,
        excluded=excluded_list,
    )


# ---------------------------------------------------------------------------
# Long-VIX co-firing
# ---------------------------------------------------------------------------


def _normalize_ticker(raw: str) -> str:
    return str(raw).split("::")[-1].split("//")[0].strip().upper()


def _long_vix_value(day_holdings: Mapping[str, float]) -> tuple[float, float, bool]:
    """(long-VIX value, total value, holds a long-VIX ticker) for one symphony-day."""
    long_value = total = 0.0
    held = False
    for ticker, amount in day_holdings.items():
        total += amount
        if _normalize_ticker(ticker) in LONG_VIX_TICKERS:
            long_value += amount
            held = True
    return long_value, total, held


def _known_day(day: Mapping, live: set[str]) -> dict[str, dict[str, float]] | None:
    """The live symphonies' holdings of one day as numbers, or None when any value is unknown
    (the day's total is then unknown too, so the day is treated as missing)."""
    parsed = {
        sym: {ticker: _finite(value) for ticker, value in held.items()}
        for sym, held in day.items()
        if sym in live
    }
    if any(value is None for held in parsed.values() for value in held.values()):
        return None
    return parsed  # type: ignore[return-value]


def _failure_reason(failure: str) -> str:
    return _FAILURE_REASONS.get(failure, _UNRECOGNIZED_FAILURE_REASON)


def long_vix_cofiring(
    holdings: HoldingsInput | None, subject_id: str, live_ids: Sequence[str], window: dict
) -> dict:
    """Days the subject held a long-VIX product, days another symphony did too, and the
    peak long-VIX share of the portfolio with and without the subject. Informational."""

    def unavailable(reason: str) -> dict:
        return _unavailable(
            _LONG_VIX_FIGURES, reason, LONG_VIX_LABEL, _LONG_VIX_SUBJECT, _LONG_VIX_SOURCE
        )

    if holdings is None:
        return unavailable(_HOLDINGS_UNAVAILABLE_REASON)
    if not holdings.days and holdings.failure:
        return unavailable(_failure_reason(holdings.failure))
    live = set(live_ids)
    first, last = window.get("first"), window.get("last")
    with_subject = [
        d
        for d in sorted(holdings.days)
        if first is not None
        and last is not None
        and first <= d <= last
        and subject_id in holdings.days[d]
    ]
    if not with_subject:
        return unavailable("no holdings for this symphony in the window")
    known = {d: parsed for d in with_subject if (parsed := _known_day(holdings.days[d], live))}
    if not known:
        return unavailable("holding values unknown on every day in the window")

    held_days = co_held = 0
    peak_with = peak_without = None
    for by_symphony in known.values():
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
        _LONG_VIX_SUBJECT,
        _LONG_VIX_SOURCE,
        _window_dict(first, last, len(known)),
        figures,
    )


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def _figure_value(item: Mapping, name: str) -> float | None:
    return ((item.get("figures") or {}).get(name) or {}).get("value")


def _available(evidence: Mapping, name: str) -> Mapping | None:
    item = evidence.get(name)
    return item if item is not None and item.get("status") == "available" else None


def decide_verdict(evidence: Mapping) -> dict:
    """Retire-candidate, watch, keep or no verdict, from the redundancy, backtest and
    marginal items, with the reasons and the unavailable items."""
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
    tenure: int | None
    eligible: bool
    kind: str  # one of the basis kinds
    fallback_reason: str | None
    window: dict
    series: dict[str, float]
    values: dict[str, float]

    @property
    def label(self) -> str:
        return {_KIND_COMPOSER: COMPOSER_BASIS_LABEL, _KIND_SHADOW: SHADOW_BASIS_LABEL}.get(
            self.kind, REASON_NO_HISTORY
        )

    @property
    def source(self) -> str:
        return {_KIND_COMPOSER: _COMPOSER_SOURCE, _KIND_SHADOW: _SHADOW_SOURCE}.get(
            self.kind, _NO_SOURCE
        )


def _fallback_reason(sym: SymphonyInput) -> str:
    """Why the Composer series was not the basis: the failure class if there was one,
    otherwise an empty answer or a series too short inside the tenure."""
    if sym.series_failure:
        return _failure_reason(sym.series_failure)
    if not sym.composer_values:
        return _FAILURE_REASONS["empty"]
    return _SHORT_SERIES_REASON


def _prepare(sym: SymphonyInput, as_of: date, threshold: int) -> _Prep:
    start_day = _parse_date(sym.invested_since) if sym.invested_since else None
    if start_day is not None and start_day < CALENDAR_FLOOR:
        start_day = None
    if start_day is None:
        tenure = None
    elif start_day > as_of:
        tenure = 0
    else:
        tenure = count_trading_days(start_day, as_of)
    start = start_day or date.min

    kind = _KIND_NONE
    fallback = None
    window: dict = {"first": None, "last": None, "days": 0, "dates": [], "returns_pct": []}
    series: dict[str, float] = {}
    values: dict[str, float] = {}

    levels = _points_by_date(sym.composer_values or {}, start, as_of, _usable_level)
    if sym.series_failure is None and len(levels) >= _MIN_COMPOSER_POINTS:
        kind = _KIND_COMPOSER
        window = realized_window(sym.composer_values, start, as_of)
        values = {d.isoformat(): level for d, level in levels.items()}
        series = dict(zip(window["dates"][1:], window["returns_pct"], strict=True))
    else:
        fallback = _fallback_reason(sym)
        rows = _points_by_date(sym.shadow_returns or {}, start, as_of, _finite)
        if rows:
            ordered = sorted(rows)
            kind = _KIND_SHADOW
            dates = [d.isoformat() for d in ordered]
            returns = [rows[d] for d in ordered]
            series = dict(zip(dates, returns, strict=True))
            window = {
                "first": dates[0],
                "last": dates[-1],
                "days": len(dates),
                "dates": dates,
                "returns_pct": returns,
            }
    eligible = tenure is not None and tenure >= threshold
    return _Prep(sym, tenure, eligible, kind, fallback, window, series, values)


def qualifying_window(symphony: SymphonyInput, as_of: date, threshold: int) -> dict | None:
    """The first and last date and day count of the realized window the review would use
    for this symphony, or None when it is not eligible or has no history. A backtest
    requested over exactly these dates is accepted by the review."""
    prep = _prepare(symphony, as_of, threshold)
    if not prep.eligible or prep.kind == _KIND_NONE:
        return None
    return _window_dict(prep.window["first"], prep.window["last"], prep.window["days"])


def _withheld_reason(p: _Prep) -> str | None:
    """Why no evidence can be shown for this symphony, or None when it can be scored."""
    if p.tenure is None:
        return REASON_TENURE_UNAVAILABLE
    if not p.eligible:
        return REASON_NOT_YET_ELIGIBLE
    if p.kind == _KIND_NONE:
        return REASON_NO_HISTORY
    return None


def _performance_item(p: _Prep) -> tuple[dict, dict]:
    metrics = performance_metrics(p.window["returns_pct"])
    if all(v is None for v in metrics.values()):
        reason = "not computable: fewer than two finite returns"
        return _unavailable(_PERF_FIGURES, reason, p.label, _PERFORMANCE_SUBJECT, p.source), {}
    window = _window_dict(p.window["first"], p.window["last"], p.window["days"])
    figures = {
        name: _figure(metrics[name], unit, "undefined for this series")
        for name, unit in _PERF_FIGURES
    }
    item = _item("available", None, p.label, _PERFORMANCE_SUBJECT, p.source, window, figures)
    return item, metrics


def _composite_item(
    p: _Prep, reason: str | None, composite: float | None, window: dict, population: int
) -> dict:
    if reason is not None or composite is None:
        return _unavailable(
            _COMPOSITE_FIGURES,
            reason or _NOT_COMPUTABLE,
            p.label,
            _COMPOSITE_SUBJECT,
            p.source,
        )
    return _item(
        "available",
        None,
        p.label,
        _COMPOSITE_SUBJECT,
        p.source,
        dict(window),
        {"composite": _figure(composite, _COMPOSITE_UNIT)},
        population_size=population,
    )


def _note_for_sibling(sibling_reason: str | None) -> str | None:
    if sibling_reason is None:
        return None
    if sibling_reason == _BASIS_DIFFERS_REASON:
        return f"{_SIBLING_NOTE_PREFIX}{_BASIS_DIFFERS_NOTE}"
    return f"{_SIBLING_NOTE_PREFIX}{sibling_reason}"


def _pair_figures(pair: dict, sibling: _Prep, note: str | None) -> dict:
    return {
        "sibling_id": sibling.sym.symphony_id,
        "sibling_name": sibling.sym.display_name,
        "correlation": _figure(pair["correlation"], "correlation", "correlation is undefined"),
        "ci_lower": _figure(pair["ci_lower"], "correlation", _INTERVAL_NOT_COMPUTED_REASON),
        "ci_upper": _figure(pair["ci_upper"], "correlation", _INTERVAL_NOT_COMPUTED_REASON),
        "stressed_correlation": _figure(
            pair["stressed_correlation"], "correlation", "stressed-window correlation not computed"
        ),
        "holdings_overlap": _figure(
            pair["holdings_overlap"], "ratio", _HOLDINGS_UNAVAILABLE_REASON
        ),
        "n_obs": pair["n_obs"],
        "uncertainty_gate": pair["uncertainty_gate"],
        "structural_gate": pair["structural_gate"],
        "note": note,
    }


def _pairs_of(sid: str, pairs: Sequence[dict]) -> list[dict]:
    return [x for x in pairs if sid in (x["sym_a"], x["sym_b"])]


def _sibling_of(sid: str, pair: dict) -> str:
    return pair["sym_b"] if pair["sym_a"] == sid else pair["sym_a"]


def _flagged_and_shown(sid: str, mine: Sequence[dict], scores: Mapping) -> tuple[list, dict | None]:
    """Pairs that flag ``sid`` for retirement (both gates passed and ``sid`` the weaker
    member) and the pair its row shows: the most correlated flagging pair, else the most
    correlated screen hit that had enough aligned days to be evaluated. The recommender
    likewise keeps the most correlated pair."""
    flagged = [
        x
        for x in mine
        if _passes_both_gates(x)
        and retirement_recommender.select_retirement_candidate(x["sym_a"], x["sym_b"], scores)
        == sid
    ]
    evaluable_hits = [
        x for x in mine if x["screen_hit"] and x["uncertainty_gate"]["state"] != _GATE_INSUFFICIENT
    ]
    pool = flagged or evaluable_hits
    return flagged, max(pool, key=lambda x: x["correlation"]) if pool else None


def _redundancy_unavailable(p: _Prep, reason: str) -> dict:
    return _item(
        "unavailable",
        reason,
        p.label,
        _REDUNDANCY_SUBJECT,
        _REDUNDANCY_SOURCE,
        None,
        {},
        retire_candidate=False,
        pair=None,
    )


def _no_pair_item(p: _Prep, preps: Sequence[_Prep]) -> dict:
    """The redundancy item of a symphony that belongs to no evaluated pair."""
    peers = [q for q in preps if q is not p]
    if not peers:
        return _item(
            "not_applicable",
            "no other symphonies to compare",
            p.label,
            _REDUNDANCY_SUBJECT,
            _REDUNDANCY_SOURCE,
            None,
            {},
            retire_candidate=False,
            pair=None,
        )
    if any(q.kind not in (_KIND_NONE, p.kind) for q in peers):
        return _redundancy_unavailable(p, _BASES_DIFFER_REASON)
    return _redundancy_unavailable(p, "no history for any other symphony")


def _redundancy_item(
    p: _Prep, preps: Sequence[_Prep], by_id: Mapping, pairs: Sequence[dict], scores, reasons
) -> dict:
    sid = p.sym.symphony_id
    mine = _pairs_of(sid, pairs)
    if not mine:
        return _no_pair_item(p, preps)
    if all(x["uncertainty_gate"]["state"] == _GATE_INSUFFICIENT for x in mine):
        return _redundancy_unavailable(p, _insufficient_text(retirement_recommender.MIN_OBS_FLOOR))

    flagged, shown = _flagged_and_shown(sid, mine, scores)
    pair = window = None
    extra = {"summary": _NO_SIBLING_TEXT}
    if shown is not None:
        sibling = by_id[_sibling_of(sid, shown)]
        note = _note_for_sibling(reasons.get(sibling.sym.symphony_id))
        pair = _pair_figures(shown, sibling, note)
        window = shown["window"]
        extra = {}
    return _item(
        "available",
        None,
        p.label,
        _REDUNDANCY_SUBJECT,
        _REDUNDANCY_SOURCE,
        window,
        {},
        retire_candidate=bool(flagged),
        pair=pair,
        **extra,
    )


def _recommendation(
    candidate_id: str, pair: dict, scores: Mapping, composite_window: dict, as_of: date
) -> dict:
    """The evidence dict the existing recommender stores for a flagged pair, plus the
    window keys a later night's reuse comparison needs. Metrics keep the recommender's
    key names and its scale."""
    sibling_id = _sibling_of(candidate_id, pair)
    candidate, sibling = scores[candidate_id], scores[sibling_id]
    window = pair["window"]
    return _clean(
        {
            "candidate_id": candidate_id,
            "sibling_id": sibling_id,
            "correlation": pair["correlation"],
            "ci_lower": pair["ci_lower"],
            "ci_upper": pair["ci_upper"],
            "n_obs": pair["n_obs"],
            "candidate_composite": candidate.composite,
            "sibling_composite": sibling.composite,
            "candidate_metrics": dict(candidate.metrics),
            "sibling_metrics": dict(sibling.metrics),
            "uncertainty_gate_passed": pair["uncertainty_gate"]["state"] == _GATE_PASSED,
            "structural_redundancy_gate_passed": pair["structural_gate"]["state"] == _GATE_PASSED,
            "stressed_correlation": pair["stressed_correlation"],
            "holdings_overlap": pair["holdings_overlap"],
            "basis_label": COMPOSER_BASIS_LABEL,
            "as_of_trading_day": as_of.isoformat(),
            "window_first": window["first"],
            "window_last": window["last"],
            "window_days": window["days"],
            "composite_window_first": composite_window["first"],
            "composite_window_last": composite_window["last"],
            "composite_window_days": composite_window["days"],
        }
    )


def build_review(inputs: ReviewInputs) -> ReviewResult:
    """Assemble the six evidence items and the verdict for every symphony, and the
    recommendation for every retire-candidate."""
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
        withheld = _withheld_reason(p)
        if withheld is not None:
            composite_reason[sid] = withheld
        elif p.kind != _KIND_COMPOSER:
            composite_reason[sid] = _BASIS_DIFFERS_REASON
        else:
            covered = [k for k in session_keys if k in p.values]
            if len(covered) < len(session_keys):
                composite_reason[sid] = (
                    f"insufficient coverage in the common window ({len(covered)} of "
                    f"{len(session_keys)} days)"
                )
                continue
            window = realized_window({k: p.values[k] for k in covered}, sessions[0], sessions[-1])
            metrics_by_id[sid] = _scored_metrics(window["returns_pct"])
            composite_reason[sid] = None
    scores = retirement_recommender.compute_composite_scores(metrics_by_id)
    for sid, score in scores.items():
        if not score.eligible:
            composite_reason[sid] = "not computable: a composite metric is undefined"
    population = sum(1 for score in scores.values() if score.eligible)

    # Redundancy pairs, computed within one basis only.
    tickers = {p.sym.symphony_id: p.sym.current_tickers for p in preps if p.sym.current_tickers}
    pairs: list[dict] = []
    for kind in (_KIND_COMPOSER, _KIND_SHADOW):
        group = {p.sym.symphony_id: p.series for p in preps if p.kind == kind}
        pairs.extend(evaluate_redundancy(group, tickers))

    # Marginal inputs: scored symphonies compare; every other one is listed with its reason.
    eligible_inputs = {
        p.sym.symphony_id: {
            "basis": p.kind,
            "values": p.values if p.kind == _KIND_COMPOSER else None,
        }
        for p in preps
        if _withheld_reason(p) is None
    }
    excluded = [
        {"display_name": p.sym.display_name, "reason": f"{_EXCLUDED_PREFIX}{reason}"}
        for p in preps
        if (reason := _withheld_reason(p)) is not None
    ]

    entries = [
        _build_entry(
            p,
            preps=preps,
            by_id=by_id,
            pairs=pairs,
            scores=scores,
            composite_reason=composite_reason,
            composite_window=composite_window,
            population=population,
            eligible_inputs=eligible_inputs,
            excluded=excluded,
            holdings=inputs.holdings,
            live_ids=live_ids,
            threshold=threshold,
        )
        for p in preps
    ]

    recommendations = []
    for p in sorted(preps, key=lambda q: q.sym.symphony_id):
        sid = p.sym.symphony_id
        flagged, shown = _flagged_and_shown(sid, _pairs_of(sid, pairs), scores)
        if flagged and shown is not None:
            recommendations.append(
                _recommendation(sid, shown, scores, composite_window, as_of),
            )
    return ReviewResult(
        run_id=inputs.run_id,
        trigger=inputs.trigger,
        started_at=inputs.started_at,
        as_of=as_of,
        threshold=inputs.threshold,
        composite_window=composite_window,
        entries=entries,
        recommendations=recommendations,
    )


def _eligibility(p: _Prep, threshold: int) -> tuple[int | None, str]:
    """(trading days remaining until eligible, the text that says so)."""
    if p.tenure is None:
        return None, f"{REASON_TENURE_UNAVAILABLE}: Composer did not report an invested-since date"
    if p.tenure < threshold:
        remaining = threshold - p.tenure
        unit = "trading day" if remaining == 1 else "trading days"
        return remaining, f"{REASON_NOT_YET_ELIGIBLE}: {remaining} {unit} remaining"
    return None, "eligible"


def _withheld_evidence(p: _Prep, reason: str, excluded: Sequence[Mapping]) -> dict:
    """Six unavailable items that all give the one reason the symphony cannot be scored."""
    return {
        "performance": _unavailable(_PERF_FIGURES, reason, p.label, _PERFORMANCE_SUBJECT, p.source),
        "composite": _unavailable(
            _COMPOSITE_FIGURES, reason, p.label, _COMPOSITE_SUBJECT, p.source
        ),
        "redundancy": _redundancy_unavailable(p, reason),
        "backtest": _backtest_unavailable(reason),
        "marginal": _unavailable(
            _MARGINAL_FIGURES,
            reason,
            MARGINAL_LABEL,
            _MARGINAL_SUBJECT,
            _COMPOSER_SOURCE,
            excluded=[dict(record) for record in excluded],
        ),
        "long_vix": _unavailable(
            _LONG_VIX_FIGURES, reason, LONG_VIX_LABEL, _LONG_VIX_SUBJECT, _LONG_VIX_SOURCE
        ),
    }


def _scored_evidence(
    p: _Prep,
    *,
    preps,
    by_id,
    pairs,
    scores,
    composite_reason,
    composite_window,
    population,
    eligible_inputs,
    excluded,
    holdings,
    live_ids,
) -> dict:
    sid = p.sym.symphony_id
    performance, metrics = _performance_item(p)
    score = scores.get(sid)
    composite = _composite_item(
        p,
        composite_reason.get(sid),
        score.composite if score is not None else None,
        composite_window,
        population,
    )
    if performance["status"] == "available":
        backtest = backtest_comparison(
            window=performance["window"],
            realized_total_return=metrics.get("total_return"),
            realized_max_drawdown=metrics.get("max_drawdown"),
            backtest=p.sym.backtest,
            definition_available=p.sym.definition_available,
        )
    else:
        backtest = _backtest_unavailable(performance["reason"])
    return {
        "performance": performance,
        "composite": composite,
        "redundancy": _redundancy_item(p, preps, by_id, pairs, scores, composite_reason),
        "backtest": backtest,
        "marginal": marginal_contribution(sid, eligible_inputs, excluded),
        "long_vix": long_vix_cofiring(
            holdings, sid, live_ids, {"first": p.window["first"], "last": p.window["last"]}
        ),
    }


def _build_entry(p: _Prep, *, threshold: int, excluded: Sequence[Mapping], **review) -> dict:
    sym = p.sym
    name = sym.display_name
    days_remaining, text = _eligibility(p, threshold)
    entry = {
        "symphony_id": sym.symphony_id,
        "display_name": name,
        "invested_since": sym.invested_since,
        "tenure_trading_days": p.tenure,
        "eligibility_text": text,
        "eligible": p.eligible,
        "days_remaining": days_remaining,
        "basis": {
            "kind": p.kind,
            "label": p.label,
            "fallback_reason": p.fallback_reason if p.kind != _KIND_COMPOSER else None,
            "series_begins": p.window["first"] if p.kind != _KIND_NONE else None,
            "tenure_begins": sym.invested_since,
        },
    }
    withheld = _withheld_reason(p)
    evidence = (
        _withheld_evidence(p, withheld, excluded)
        if withheld is not None
        else _scored_evidence(p, excluded=excluded, **review)
    )
    for item in evidence.values():
        item["subject"] = f"{name}: {item['subject']}"
    if p.eligible:
        entry.update(decide_verdict(evidence))
    else:
        entry.update(
            verdict=None,
            verdict_text=f"no verdict: {withheld}",
            verdict_reasons=[],
            missing_items=[],
        )
    entry["evidence"] = evidence
    return entry


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
        """Claim the single run slot; False when a run is already in progress."""
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
        """Release the slot and forget the last failed attempt."""
        with self._mutex:
            self._free()
            self._last_failed = None

    def finish_failure(self, reason: str, at: datetime | str) -> None:
        """Release the slot and remember why the run failed and when."""
        with self._mutex:
            self._free()
            self._last_failed = {
                "at": at.isoformat() if isinstance(at, datetime) else str(at),
                "reason": reason,
            }

    def release(self) -> None:
        """Release the slot without recording an outcome."""
        with self._mutex:
            self._free()

    def snapshot(self) -> dict:
        """The current state: running or not, since when, by which trigger, last failure."""
        with self._mutex:
            return {
                "running": self._running,
                "running_since": self._running_since,
                "trigger": self._trigger,
                "last_failed_attempt": dict(self._last_failed) if self._last_failed else None,
            }


# The one process-wide review run state (see the class docstring).
REVIEW_RUN_STATE = _ReviewRunState()
