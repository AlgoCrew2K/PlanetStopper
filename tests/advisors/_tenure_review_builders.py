"""TEST-ONLY builders and independent oracles for the tenure review suites.

Nothing here is imported by production code. Two responsibilities:

1. Builders for the review module's input dataclasses and for deterministic
   series with controlled correlation structure (orthogonal sinusoid pairs, an
   integer pattern whose Pearson r is exactly the float 0.65, calm-plus-stress
   block pairs).
2. Oracles written from the spec text and from textbook formulas, never from
   the module under test: an NYSE holiday list for trading-day counting, the
   percent-scale return metrics, the composite weights, the Fisher interval and
   the stressed-window correlation.

Return series are PERCENT-scale (0.5 means +0.5%) unless a name says
``fraction``. Max drawdown is a percent-scale number that is <= 0.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from functools import cache, lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

import advisors.tenure_review as tr
from advisors import correlation_diagnostic
from tests.advisors._retirement_recommender_reference import (
    reference_fisher_ci_lower,
    reference_fisher_ci_upper,
)

ET = ZoneInfo("America/New_York")

# Fixed run identity used by every builder so two runs differ only where a test
# changes something on purpose.
RUN_ID = "0123456789abcdef0123456789abcdef"

# ---------------------------------------------------------------------------
# Calendar: a holiday list that does not come from the module under test or from
# market_calendar. Valid for 2025-02-01 .. 2027-01-01 (the range the tests use).
# ---------------------------------------------------------------------------

_NYSE_HOLIDAYS = frozenset(
    date(*ymd)
    for ymd in (
        # 2025 (Presidents Day, Good Friday, Memorial Day, Juneteenth, Independence
        # Day, Labor Day, Thanksgiving, Christmas)
        (2025, 2, 17),
        (2025, 4, 18),
        (2025, 5, 26),
        (2025, 6, 19),
        (2025, 7, 4),
        (2025, 9, 1),
        (2025, 11, 27),
        (2025, 12, 25),
        # 2026 (New Year, MLK, Presidents Day, Good Friday, Memorial Day,
        # Juneteenth, Independence Day observed, Labor Day, Thanksgiving, Christmas)
        (2026, 1, 1),
        (2026, 1, 19),
        (2026, 2, 16),
        (2026, 4, 3),
        (2026, 5, 25),
        (2026, 6, 19),
        (2026, 7, 3),
        (2026, 9, 7),
        (2026, 11, 26),
        (2026, 12, 25),
        # 2027
        (2027, 1, 1),
    )
)
_ORACLE_RANGE = (date(2025, 2, 1), date(2027, 1, 1))


def oracle_count_trading_days(start: date, end: date) -> int:
    """Inclusive count of NYSE trading days, from the holiday list above."""
    assert _ORACLE_RANGE[0] <= start and end <= _ORACLE_RANGE[1], "outside the oracle range"
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in _NYSE_HOLIDAYS:
            n += 1
        d += timedelta(days=1)
    return n


_NYSE = mcal.get_calendar("XNYS")


@cache
def nyse_days(start: date, end: date) -> tuple[date, ...]:
    """Every NYSE trading day in [start, end], ascending, from the calendar library."""
    idx = _NYSE.valid_days(start_date=start.isoformat(), end_date=end.isoformat())
    return tuple(ts.date() for ts in idx)


def last_n_nyse_days(end: date, n: int) -> list[date]:
    """The n NYSE trading days ending at ``end`` (``end`` must be a trading day)."""
    days = nyse_days(end - timedelta(days=int(n * 1.6) + 20), end)
    assert days[-1] == end, "end must be a trading day"
    assert len(days) >= n
    return list(days[-n:])


def et(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0):
    return datetime(year, month, day, hour, minute, second, tzinfo=ET)


# ---------------------------------------------------------------------------
# Series builders (percent-scale daily returns)
# ---------------------------------------------------------------------------


def wave_returns(
    n: int, *, amp: float = 0.5, drift: float = 0.0, freq: float = 0.37, phase: float = 0.0
) -> list[float]:
    return [drift + amp * math.sin(freq * i + phase) + 0.02 * ((i % 7) - 3) for i in range(n)]


def values_from_returns(dates: list[date], returns_pct: list[float], base: float = 100.0):
    """Deposit-adjusted value series: ``base`` on dates[0], then compounded by the
    returns (returns_pct[i] is the return INTO dates[i+1])."""
    assert len(returns_pct) == len(dates) - 1
    out = {dates[0].isoformat(): base}
    v = base
    for d, r in zip(dates[1:], returns_pct, strict=True):
        v = v * (1.0 + r / 100.0)
        out[d.isoformat()] = v
    return out


def returns_from_values(values: dict[str, float]) -> list[float]:
    keys = sorted(values)
    return [(values[b] / values[a] - 1.0) * 100.0 for a, b in zip(keys, keys[1:], strict=False)]


def sine_returns(n: int, k: int, *, amp: float = 0.5, drift: float = 0.05) -> list[float]:
    """n percent returns that are exactly orthogonal across different integer k."""
    return [drift + amp * math.sin(2 * math.pi * k * i / n) for i in range(n)]


def orthogonal_pair(n: int, rho: float, k: int = 1, scale: float = 1.0):
    """Two zero-mean series of equal norm whose sample Pearson r is ``rho`` (sin and
    cos of one integer frequency over a full period are exactly orthogonal)."""
    u = [math.sin(2 * math.pi * k * i / n) for i in range(n)]
    v = [math.cos(2 * math.pi * k * i / n) for i in range(n)]
    s = math.sqrt(1.0 - rho * rho)
    return [scale * a for a in u], [scale * (rho * a + s * b) for a, b in zip(u, v, strict=True)]


# Zero-sum integer patterns (length 10) with sum of squares 20 and cross product 13:
# tiled and scaled by a power of two, every sum in Pearson r is exact in binary
# floating point, so r is the float 0.65 exactly (13 / 20).
_EXACT_065_P = (1, -2, 0, -1, 2, 0, -1, 2, -2, 1)
_EXACT_065_Q = (1, -1, -1, -2, 2, 0, 2, 1, -2, 0)


def exact_threshold_pair(tiles: int = 20):
    scale = 0.25
    p = [scale * v for v in _EXACT_065_P] * tiles
    q = [scale * v for v in _EXACT_065_Q] * tiles
    return p, q


def calm_block(n: int, *, freq: float = 0.4, amp: float = 0.1):
    a = [amp * math.sin(i * freq) + 0.05 * amp * ((i % 7) - 3) for i in range(n)]
    b = [0.95 * v + 0.02 * amp * ((i % 5) - 2) for i, v in enumerate(a)]
    return a, b


def stress_block(n: int, kind: str, *, amp: float = 0.23, freq: float = 0.9):
    """A short run of large moves at the end of a series. ``redundant``: the second
    series follows the first. ``diverging``: the second series is unrelated."""
    a = [amp * math.sin(i * freq) for i in range(n)]
    if kind == "redundant":
        b = [0.92 * v + 0.03 * amp * ((i % 3) - 1) for i, v in enumerate(a)]
    elif kind == "diverging":
        b = [amp * math.cos(i * 1.7 + 0.4) for i in range(n)]
    else:
        raise ValueError(kind)
    return a, b


def block_pair(
    total: int,
    kind: str,
    *,
    stress_n: int = 15,
    freq: float = 0.4,
    stress_freq: float = 0.9,
    stress_amp: float = 0.23,
):
    """``total`` aligned returns: a calm block then a stress block (see stress_block)."""
    ca, cb = calm_block(total - stress_n, freq=freq)
    sa, sb = stress_block(stress_n, kind, amp=stress_amp, freq=stress_freq)
    return ca + sa, cb + sb


# ---------------------------------------------------------------------------
# Independent oracles
# ---------------------------------------------------------------------------

W_CAGR, W_SHARPE, W_SORTINO, W_MAXDD, W_CALMAR = 0.40, 0.20, 0.15, 0.15, 0.10
COMPOSITE_WEIGHTS = {
    "annualized_return": W_CAGR,
    "sharpe": W_SHARPE,
    "sortino": W_SORTINO,
    "max_drawdown": W_MAXDD,
    "calmar": W_CALMAR,
}
TRADING_DAYS_PER_YEAR = 252


def oracle_metrics(returns_pct: list[float]) -> dict[str, float | None]:
    """Percent-scale return metrics from textbook formulas. Sharpe and Sortino are
    daily, non-annualized ratios; None when the denominator is zero."""
    n = len(returns_pct)
    f = [x / 100.0 for x in returns_pct]
    w = 1.0
    peak = 1.0
    mdd = 0.0
    for x in f:
        w *= 1.0 + x
        peak = max(peak, w)
        mdd = min(mdd, w / peak - 1.0)
    ann = w ** (TRADING_DAYS_PER_YEAR / n) - 1.0
    mean = sum(f) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in f) / (n - 1))
    downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in f) / n)
    return {
        "total_return": (w - 1.0) * 100.0,
        "annualized_return": ann * 100.0,
        "max_drawdown": mdd * 100.0,
        "sharpe": mean / sd if sd > 0 else None,
        "sortino": mean / downside if downside > 0 else None,
        "calmar": ann / abs(mdd) if mdd != 0 else None,
    }


def oracle_composites(metrics_by_symphony: dict[str, dict]) -> dict[str, float | None]:
    """Min-max across symphonies with all five metrics present, spec weights, 0.5 for a
    metric with no spread."""
    keys = tuple(COMPOSITE_WEIGHTS)
    usable = {s: m for s, m in metrics_by_symphony.items() if all(m[k] is not None for k in keys)}
    out: dict[str, float | None] = {s: None for s in metrics_by_symphony}
    for s, m in usable.items():
        total = 0.0
        for k in keys:
            lo = min(u[k] for u in usable.values())
            hi = max(u[k] for u in usable.values())
            norm = (m[k] - lo) / (hi - lo) if hi > lo else 0.5
            total += COMPOSITE_WEIGHTS[k] * norm
        out[s] = total
    return out


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy)


# The stressed window is the ceil(5% of n) aligned days with the most negative
# combined return, and it needs at least 10 of them. Integer arithmetic keeps the
# ceiling exact where a float product could land on the wrong side.
STRESS_FRACTION_PERCENT = 5
STRESS_MIN_DAYS = 10
UNCERTAINTY_MIN_DAYS = 30
SCREEN_THRESHOLD = 0.65


def stress_day_count(n: int) -> int:
    return -(-n * STRESS_FRACTION_PERCENT // 100)


def days_needed_for_structural_gate() -> int:
    n = 1
    while stress_day_count(n) < STRESS_MIN_DAYS:
        n += 1
    return n


def oracle_stressed_correlation(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    k = stress_day_count(n)
    if k < STRESS_MIN_DAYS:
        return None
    combined = [(a[i] + b[i]) / 2.0 for i in range(n)]
    idx = sorted(range(n), key=lambda i: combined[i])[:k]
    xs = [a[i] for i in idx]
    ys = [b[i] for i in idx]
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return pearson(xs, ys)


def oracle_ci(r: float, n: int) -> tuple[float, float]:
    return reference_fisher_ci_lower(r, n), reference_fisher_ci_upper(r, n)


def exact_pearson_or_none(xs: list[float], ys: list[float]):
    """The correlation primitive the recommender itself uses, for fixture sanity."""
    return correlation_diagnostic._pearson_r(xs, ys)  # noqa: SLF001


def by_date(dates: list[date], returns_pct: list[float]) -> dict[str, float]:
    assert len(dates) == len(returns_pct)
    return {d.isoformat(): r for d, r in zip(dates, returns_pct, strict=True)}


# ---------------------------------------------------------------------------
# Builders for the module's input dataclasses
# ---------------------------------------------------------------------------


def make_threshold(value: int, note: str | None = None):
    return tr.ThresholdSetting(value=value, note=note)


def make_symphony(
    symphony_id: str,
    display_name: str,
    invested_since: str | None,
    *,
    composer_values: dict | None = None,
    series_failure: str | None = None,
    shadow_returns: dict | None = None,
    current_tickers=None,
    backtest=None,
    definition_available: bool = True,
):
    return tr.SymphonyInput(
        symphony_id=symphony_id,
        display_name=display_name,
        invested_since=invested_since,
        composer_values=composer_values,
        series_failure=series_failure,
        shadow_returns=shadow_returns,
        current_tickers=current_tickers,
        backtest=backtest,
        definition_available=definition_available,
    )


def composer_symphony(
    symphony_id: str,
    display_name: str,
    dates: list[date],
    returns_pct: list[float],
    *,
    invested_since: str | None = "from-dates",
    **kwargs,
):
    """A Composer-basis symphony holding one value per date; its tenure starts on
    dates[0] unless ``invested_since`` says otherwise. len(returns_pct) is
    len(dates) minus 1: the return INTO each date after the first."""
    start = dates[0].isoformat() if invested_since == "from-dates" else invested_since
    return make_symphony(
        symphony_id,
        display_name,
        start,
        composer_values=values_from_returns(dates, returns_pct),
        **kwargs,
    )


def make_inputs(
    symphonies,
    *,
    as_of: date,
    threshold: int | object = 126,
    holdings=None,
    trigger: str = "nightly",
    run_id: str = RUN_ID,
    started_at: datetime | None = None,
):
    setting = make_threshold(threshold) if isinstance(threshold, int) else threshold
    return tr.ReviewInputs(
        run_id=run_id,
        trigger=trigger,
        started_at=started_at or et(as_of.year, as_of.month, as_of.day, 17, 0),
        as_of=as_of,
        threshold=setting,
        symphonies=list(symphonies),
        holdings=holdings,
    )


def review(symphonies, *, as_of: date, threshold: int | object = 126, holdings=None):
    return tr.build_review(
        make_inputs(symphonies, as_of=as_of, threshold=threshold, holdings=holdings)
    )


def entry_of(result, symphony_id: str) -> dict:
    matches = [e for e in result.entries if e["symphony_id"] == symphony_id]
    assert len(matches) == 1, f"expected exactly one entry for {symphony_id}, got {len(matches)}"
    return matches[0]


def item_of(entry: dict, name: str) -> dict:
    return entry["evidence"][name]


def figure_of(entry: dict, item: str, name: str) -> dict:
    return entry["evidence"][item]["figures"][name]


def value_of(entry: dict, item: str, name: str):
    return figure_of(entry, item, name)["value"]


EVIDENCE_ITEMS = ("performance", "composite", "redundancy", "backtest", "marginal", "long_vix")


def iter_figures(obj):
    """Every figure dict (a dict holding both ``value`` and ``unit``) anywhere in obj."""
    if isinstance(obj, dict):
        if "value" in obj and "unit" in obj:
            yield obj
        for v in obj.values():
            yield from iter_figures(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from iter_figures(v)


# ---------------------------------------------------------------------------
# Rosters shared by more than one suite
# ---------------------------------------------------------------------------

AS_OF = date(2026, 12, 31)


def roster_with_ineligible_correlated_sibling():
    """Threshold 250. ``keep-a`` and ``keep-b`` (260 points) are eligible; ``young-c``
    (200 points, so 199 returns) is not. ``keep-a`` and ``young-c`` are almost the same
    series over their 199 shared days (both gates pass), and ``young-c`` is the stronger
    performer, so ``keep-a`` would be the weaker member of a passing pair IF ``young-c``
    were scored. ``keep-b`` is unrelated to both."""
    n_returns = 259
    ca, _ = calm_block(n_returns - 15)
    sa, _ = stress_block(15, "redundant")
    a_ret = ca + sa
    b_ret = [0.12 * math.sin(0.23 * i + 1.0) + 0.03 for i in range(n_returns)]
    c_ret = [0.9 * v + 0.05 + 0.003 * ((i % 5) - 2) for i, v in enumerate(a_ret[-199:])]
    days = last_n_nyse_days(AS_OF, 260)
    return (
        [
            composer_symphony("keep-a", "Alpha Keeper", days, a_ret),
            composer_symphony("keep-b", "Bravo Keeper", days, b_ret),
            composer_symphony("young-c", "Charlie Newcomer", days[-200:], c_ret),
        ],
        250,
    )


def roster_with_two_redundant_candidates():
    """Threshold 250, three eligible symphonies over 265 returns. ``tail-x`` and
    ``tail-y1`` have the highest correlation of any pair but their stressed-window
    returns diverge (structural gate fails). ``tail-x`` and ``tail-y2`` are slightly
    less correlated and move together under stress (both gates pass); ``tail-y2``
    outperforms, so ``tail-x`` is the weaker member of the passing pair."""
    n_returns = 265
    stress_n = 15
    xc, _ = calm_block(n_returns - stress_n)
    xs = [-1.0 + 0.05 * math.sin(0.9 * i) for i in range(stress_n)]
    x = xc + xs
    y1 = [0.99 * v + 0.002 * ((i % 5) - 2) for i, v in enumerate(xc)] + [
        -1.0 + 0.05 * math.cos(1.7 * i + 0.4) for i in range(stress_n)
    ]
    y2 = [0.9 * v + 0.01 * ((i % 5) - 2) + 0.04 for i, v in enumerate(xc)] + [
        0.9 * v + 0.04 for v in xs
    ]
    days = last_n_nyse_days(AS_OF, n_returns + 1)
    return (
        [
            composer_symphony("tail-x", "Xenon Tail", days, x),
            composer_symphony("tail-y1", "Yankee Tail One", days, y1),
            composer_symphony("tail-y2", "Yankee Tail Two", days, y2),
        ],
        250,
    )
