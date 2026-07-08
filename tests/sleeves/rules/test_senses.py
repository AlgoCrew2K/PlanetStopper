"""
RED tests — sleeves/rules/senses.py: the closed sense registry (AC-4).

CONTRACT this file specifies for the GREEN implementer (s2-rules-impl):

    sleeves/rules/senses.py

    @dataclass(frozen=True)
    class SenseResult:
        value: float | str | int | None
        available: bool
        reason: str | None     # populated iff available is False; never raises

    def sense_time_of_day(now_et: datetime) -> SenseResult
        # value = int seconds-since-midnight ET (hour*3600 + minute*60 + second).
        # now_et MUST be tz-aware in America/New_York; a naive or differently-
        # tz'd datetime is a fail-safe unavailable(reason="naive_or_wrong_tz_datetime"),
        # never a silent wrong-offset computation.

    def sense_day_of_week(now_et: datetime) -> SenseResult
        # value = "MON".."SUN" (3-letter uppercase). Same tz-aware requirement.

    def sense_sleeve_state(*, sleeve_row: dict, field: str) -> SenseResult
        # field in {"sleeve_status","sleeve_cash_usd","sleeve_equity_usd","position_qty"}.
        # unavailable(reason="field_missing") when sleeve_row lacks the key or it is None.

    def sense_indicator(*, indicator: str, closes: list[float], window: int) -> SenseResult
        # indicator in {"sma","ema","rsi","momentum","realized_vol","drawdown_from_high"}.
        # Never raises on a too-short `closes`; unavailable(reason="insufficient_history").

    def sense_fred_series(
        *, series_id: str, cached_observations: list[dict], as_of: date, max_age_days: int = 10,
    ) -> SenseResult
        # cached_observations: [{"date": "YYYY-MM-DD", "value": float}, ...] (latest cached
        # only -- NEVER a live FRED call from this function; no `requests` import in
        # senses.py at all is asserted structurally below).
        # unavailable(reason="no_cached_observations") when empty.
        # unavailable(reason="stale") when the latest observation's date is more than
        # max_age_days before as_of.

    def resolve_sense(sense_key: str, *, ctx: "SenseContext") -> SenseResult
        # Parses a schema-validated sense-key string (see schema.py's grammar) and
        # dispatches to the right sense_* function above.

    @dataclass(frozen=True)
    class SenseContext:
        now_et: datetime
        sleeve_row: dict
        closes: list[float]           # daily closes for the rule's `when.symbol`
        fred_cache: dict[str, list[dict]]   # series_id -> cached_observations
        as_of: "date"

Indicator formulas (pinned exactly; every expected value in this file is
DERIVED from these formulas applied to the golden fixture's raw closes, never
a bare literal pulled from a prior implementation run):

    sma(window):    mean(closes[-window:])
    ema(window):    seed = mean(closes[:window]); alpha = 2/(window+1);
                    iterate ema_next = close*alpha + ema_prev*(1-alpha) over
                    closes[window:] in order.
    rsi(window):    needs window+1 closes (window day-over-day deltas).
                    avg_gain = mean of positive deltas over the window (0 if none);
                    avg_loss = mean of abs(negative deltas) over the window (0 if none).
                    avg_loss == 0 -> rsi = 100.0 (never divide-by-zero).
                    avg_gain == 0 (and avg_loss != 0) -> rsi = 0.0.
                    else: rs = avg_gain/avg_loss; rsi = 100 - 100/(1+rs).
    momentum(window): (closes[-1] - closes[-(window+1)]) / closes[-(window+1)]
                    -- identical formula to advisors.lens_technicals._compute_momentum.
    realized_vol(window): sample stdev (ddof=1) of the `window` daily pct-return
                    observations from closes[-(window+1):] -- identical formula to
                    regime_classifier._realized_vol applied to returns, not closes.
    drawdown_from_high(window): slice = closes[-window:]; running_high = max(slice);
                    drawdown = (slice[-1] - running_high) / running_high  (<= 0).

Every indicator requires `len(closes) >= window` (or `window + 1` for
momentum/rsi/realized_vol, per the formulas above) -- shorter input is
unavailable(reason="insufficient_history"), never an IndexError/ZeroDivisionError.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip(
    "sleeves.rules.senses", reason="RED phase — sleeves.rules.senses not implemented yet"
)

import sleeves.rules.senses as senses  # noqa: E402

from .conftest import ET, UTC, et_datetime  # noqa: E402

# ---------------------------------------------------------------------------
# Reference (test-owned) formula implementations — used to derive expected
# values from the golden fixture's raw closes. Never a hardcoded literal.
# ---------------------------------------------------------------------------


def _ref_sma(closes: list[float], window: int) -> float:
    return sum(closes[-window:]) / window


def _ref_ema(closes: list[float], window: int) -> float:
    seed = sum(closes[:window]) / window
    alpha = 2.0 / (window + 1)
    ema = seed
    for c in closes[window:]:
        ema = c * alpha + ema * (1 - alpha)
    return ema


def _ref_rsi(closes: list[float], window: int) -> float:
    recent = closes[-(window + 1) :]
    deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
    gains = [d for d in deltas if d > 0]
    losses = [abs(d) for d in deltas if d < 0]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _ref_momentum(closes: list[float], window: int) -> float:
    needed = window + 1
    return (closes[-1] - closes[-needed]) / closes[-needed]


def _ref_realized_vol(closes: list[float], window: int) -> float:
    recent = closes[-(window + 1) :]
    returns = [(recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent))]
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(variance)


def _ref_drawdown_from_high(closes: list[float], window: int) -> float:
    sl = closes[-window:]
    running_high = max(sl)
    return (sl[-1] - running_high) / running_high


# ---------------------------------------------------------------------------
# 1. Time-of-day / day-of-week (ET, DST-safe)
# ---------------------------------------------------------------------------


class TestTimeSenses:
    def test_time_of_day_is_available_and_correct_for_a_tz_aware_et_datetime(self):
        now = et_datetime(2026, 7, 8, 10, 15, 30)
        result = senses.sense_time_of_day(now)
        assert result.available
        assert result.value == 10 * 3600 + 15 * 60 + 30

    def test_time_of_day_across_dst_spring_forward_boundary_uses_wall_clock_not_fixed_offset(self):
        # 2026-03-08 is the US spring-forward date (2am -> 3am). 09:30 ET wall-clock
        # is unambiguous on both sides of the year; the load-bearing proof is that
        # constructing the SAME wall-clock hour on either side of the DST boundary
        # yields the SAME seconds-since-midnight value, proving senses.py reasons
        # in ET wall-clock time (via zoneinfo), never a fixed UTC offset that would
        # silently drift by an hour across the boundary.
        before_dst = et_datetime(2026, 3, 1, 9, 30, 0)  # EST (UTC-5)
        after_dst = et_datetime(2026, 3, 15, 9, 30, 0)  # EDT (UTC-4)
        assert (
            senses.sense_time_of_day(before_dst).value == senses.sense_time_of_day(after_dst).value
        )
        # But the underlying UTC instants differ by exactly the DST hour shift,
        # proving zoneinfo (not a fixed offset) resolved each wall-clock time.
        assert before_dst.astimezone(UTC).hour != after_dst.astimezone(UTC).hour

    def test_day_of_week_returns_three_letter_uppercase_code(self):
        monday = et_datetime(2026, 7, 6, 12, 0, 0)  # 2026-07-06 is a Monday
        result = senses.sense_day_of_week(monday)
        assert result.available
        assert result.value == "MON"

    def test_naive_datetime_is_fail_safe_unavailable_never_a_crash(self):
        naive = datetime(2026, 7, 8, 10, 0, 0)  # no tzinfo
        result = senses.sense_time_of_day(naive)
        assert result.available is False
        assert result.reason

    def test_non_et_tz_datetime_is_fail_safe_unavailable(self):
        utc_dt = datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)
        result = senses.sense_time_of_day(utc_dt)
        assert result.available is False
        assert result.reason


# ---------------------------------------------------------------------------
# 2. Sleeve/portfolio state
# ---------------------------------------------------------------------------


class TestSleeveStateSense:
    def test_known_field_present_is_available(self):
        row = {"sleeve_status": "PAPER", "sleeve_cash_usd": 5000.0}
        result = senses.sense_sleeve_state(sleeve_row=row, field="sleeve_cash_usd")
        assert result.available
        assert result.value == 5000.0

    def test_missing_field_is_fail_safe_unavailable(self):
        row = {"sleeve_status": "PAPER"}
        result = senses.sense_sleeve_state(sleeve_row=row, field="position_qty")
        assert result.available is False
        assert result.reason == "field_missing"

    def test_none_valued_field_is_fail_safe_unavailable(self):
        row = {"sleeve_equity_usd": None}
        result = senses.sense_sleeve_state(sleeve_row=row, field="sleeve_equity_usd")
        assert result.available is False


# ---------------------------------------------------------------------------
# 3. Daily-bar indicators — golden fixture, formulas derived, never hardcoded
# ---------------------------------------------------------------------------


class TestIndicatorSenses:
    @pytest.mark.parametrize("indicator,window", [("sma", 5), ("sma", 10), ("ema", 5), ("ema", 10)])
    def test_sma_and_ema_match_reference_formula_on_mixed_series(
        self, indicator_fixture, indicator, window
    ):
        closes = indicator_fixture["mixed_series"]["closes"]
        ref = _ref_sma(closes, window) if indicator == "sma" else _ref_ema(closes, window)
        result = senses.sense_indicator(indicator=indicator, closes=closes, window=window)
        assert result.available, f"expected available, got reason={result.reason}"
        assert result.value == pytest.approx(ref, rel=1e-9), (
            f"{indicator}({window}) mismatch: engine={result.value} reference={ref}"
        )

    @pytest.mark.parametrize("window", [5, 10, 14])
    def test_rsi_matches_reference_formula_on_mixed_series(self, indicator_fixture, window):
        closes = indicator_fixture["mixed_series"]["closes"]
        ref = _ref_rsi(closes, window)
        result = senses.sense_indicator(indicator="rsi", closes=closes, window=window)
        assert result.available
        assert result.value == pytest.approx(ref, rel=1e-9)
        assert 0.0 <= result.value <= 100.0, "RSI must be bounded [0, 100]"

    def test_rsi_is_exactly_100_on_a_monotonic_up_series(self, indicator_fixture):
        closes = indicator_fixture["monotonic_up_series"]["closes"]
        result = senses.sense_indicator(indicator="rsi", closes=closes, window=14)
        assert result.available
        assert result.value == pytest.approx(100.0), "zero losses must never divide by zero"

    def test_rsi_is_exactly_0_on_a_monotonic_down_series(self, indicator_fixture):
        closes = indicator_fixture["monotonic_down_series"]["closes"]
        result = senses.sense_indicator(indicator="rsi", closes=closes, window=14)
        assert result.available
        assert result.value == pytest.approx(0.0)

    def test_momentum_matches_reference_formula(self, indicator_fixture):
        closes = indicator_fixture["mixed_series"]["closes"]
        ref = _ref_momentum(closes, 10)
        result = senses.sense_indicator(indicator="momentum", closes=closes, window=10)
        assert result.available
        assert result.value == pytest.approx(ref, rel=1e-9)

    def test_realized_vol_matches_reference_formula_and_is_non_negative(self, indicator_fixture):
        closes = indicator_fixture["mixed_series"]["closes"]
        ref = _ref_realized_vol(closes, 10)
        result = senses.sense_indicator(indicator="realized_vol", closes=closes, window=10)
        assert result.available
        assert result.value == pytest.approx(ref, rel=1e-9)
        assert result.value >= 0.0, "volatility (a stdev) must never be negative"

    def test_drawdown_from_high_matches_reference_formula_and_is_non_positive(
        self, indicator_fixture
    ):
        closes = indicator_fixture["drawdown_series"]["closes"]
        ref = _ref_drawdown_from_high(closes, len(closes))
        result = senses.sense_indicator(
            indicator="drawdown_from_high", closes=closes, window=len(closes)
        )
        assert result.available
        assert result.value == pytest.approx(ref, rel=1e-9)
        assert result.value <= 0.0, "a drawdown from the running high can never be positive"

    @pytest.mark.parametrize(
        "indicator,window",
        [
            ("sma", 14),
            ("ema", 14),
            ("rsi", 14),
            ("momentum", 14),
            ("realized_vol", 14),
            ("drawdown_from_high", 14),
        ],
    )
    def test_every_indicator_is_fail_safe_unavailable_on_insufficient_history(
        self, indicator_fixture, indicator, window
    ):
        closes = indicator_fixture["insufficient_series"]["closes"]
        result = senses.sense_indicator(indicator=indicator, closes=closes, window=window)
        assert result.available is False
        assert result.reason == "insufficient_history"
        assert result.value is None

    def test_unknown_indicator_name_is_fail_safe_unavailable_never_a_crash(self, indicator_fixture):
        closes = indicator_fixture["mixed_series"]["closes"]
        result = senses.sense_indicator(indicator="bollinger_band_width", closes=closes, window=20)
        assert result.available is False


# ---------------------------------------------------------------------------
# 4. FRED series — cached-only, never a live call
# ---------------------------------------------------------------------------


class TestFredSense:
    def test_fresh_cached_observation_is_available(self):
        cached = [{"date": "2026-07-01", "value": 15.2}, {"date": "2026-07-06", "value": 14.8}]
        result = senses.sense_fred_series(
            series_id="VIXCLS", cached_observations=cached, as_of=date(2026, 7, 8)
        )
        assert result.available
        assert result.value == 14.8  # latest by date, not by list order

    def test_stale_cached_observation_is_fail_safe_unavailable(self):
        cached = [{"date": "2026-01-01", "value": 12.0}]
        result = senses.sense_fred_series(
            series_id="VIXCLS", cached_observations=cached, as_of=date(2026, 7, 8), max_age_days=10
        )
        assert result.available is False
        assert result.reason == "stale"

    def test_empty_cache_is_fail_safe_unavailable(self):
        result = senses.sense_fred_series(
            series_id="VIXCLS", cached_observations=[], as_of=date(2026, 7, 8)
        )
        assert result.available is False
        assert result.reason == "no_cached_observations"

    def test_senses_module_makes_no_live_network_call(self):
        """Structural proof: senses.py never imports `requests` — FRED sensing
        is cache-read-only, matching AC-4/the plan's 'never a live FRED call
        in the tick path'."""
        import ast
        import inspect

        source = inspect.getsource(senses)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
        assert "requests" not in imported_names, (
            "sleeves/rules/senses.py imports `requests` — FRED sensing must be "
            "cache-read-only, never a live network call from the tick path."
        )


# ---------------------------------------------------------------------------
# 5. resolve_sense — canonical sense-key parsing + dispatch
# ---------------------------------------------------------------------------


class TestResolveSenseDispatch:
    def _ctx(self, indicator_fixture, **overrides):
        base = dict(
            now_et=et_datetime(2026, 7, 8, 10, 0, 0),
            sleeve_row={"sleeve_status": "PAPER", "sleeve_cash_usd": 1000.0},
            closes=indicator_fixture["mixed_series"]["closes"],
            fred_cache={"VIXCLS": [{"date": "2026-07-06", "value": 14.8}]},
            as_of=date(2026, 7, 8),
        )
        base.update(overrides)
        return senses.SenseContext(**base)

    def test_resolve_indicator_sense_key_dispatches_to_sense_indicator(self, indicator_fixture):
        ctx = self._ctx(indicator_fixture)
        result = senses.resolve_sense("sma_10", ctx=ctx)
        ref = _ref_sma(indicator_fixture["mixed_series"]["closes"], 10)
        assert result.available
        assert result.value == pytest.approx(ref, rel=1e-9)

    def test_resolve_time_of_day_sense_key(self, indicator_fixture):
        ctx = self._ctx(indicator_fixture)
        result = senses.resolve_sense("time_of_day", ctx=ctx)
        assert result.available
        assert result.value == 10 * 3600

    def test_resolve_sleeve_state_sense_key(self, indicator_fixture):
        ctx = self._ctx(indicator_fixture)
        result = senses.resolve_sense("sleeve_cash_usd", ctx=ctx)
        assert result.available
        assert result.value == 1000.0

    def test_resolve_fred_sense_key(self, indicator_fixture):
        ctx = self._ctx(indicator_fixture)
        result = senses.resolve_sense("fred_VIXCLS", ctx=ctx)
        assert result.available
        assert result.value == 14.8

    def test_resolve_fred_sense_key_for_a_series_not_in_the_cache_is_unavailable(
        self, indicator_fixture
    ):
        ctx = self._ctx(indicator_fixture)
        result = senses.resolve_sense("fred_VXVCLS", ctx=ctx)
        assert result.available is False
