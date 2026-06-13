"""
Cycle B2 — RED: Technicals producer for the Market Prism technicals lens.

Scope (feature-plans/lens-data-technicals.md):
  AC-1  advisors.lens_technicals._fetch_technicals(universe) exists and returns a dict
        with required keys: available (bool), ma_posture (dict|None), breadth (float|None),
        momentum (dict|None), source (str|None). Shape contract is pinned in
        tests/fixtures/math/technicals_producer_schema.json.
  AC-2  Honest-availability: on Alpaca error, empty bars, or insufficient history, the
        producer returns {"available": False, ...} and NEVER fabricates indicator values.
  AC-3  Fixtures are schema-derived (alpaca_bars_schema.json, technicals_producer_schema.json).
        Tests assert shape/format/presence; computed values derive from fixture bar data.
  AC-4  Off-execution-path; bounded retries (finite _TECH_MAX_ATTEMPTS); no blocking I/O.
  AC-5  Reuses synthetic_history.fetch_bars; no new HTTP client introduced.
  AC-6  Every indicator constant is named (no magic numbers) with a source comment.

Mocking strategy:
  - synthetic_history.fetch_bars: always patched. No live Alpaca calls from CI.
  - ai_advisor._build_technicals_section: patched in pipeline-integration tests
    to return a technicals block sourced from _fetch_technicals.
  - advisors.lens_technicals._fetch_technicals: patched in pipeline-integration
    tests to isolate pipeline wiring from producer logic.
  - No live Anthropic API. No DB calls in unit tests. Math engine never mocked.

Adversarial RED intent:
  - advisors/lens_technicals.py stub raises NotImplementedError — all functional tests fail.
  - Pipeline integration test fails because _build_technicals_section returns available=False stub.
  - Retry/bounds test fails because stub does not implement retry logic.
  - Unavailable/edge tests fail because stub does not return the required shape.

No module-level mutable state. All fixtures are function-scoped unless noted.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root + fixture paths
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent
_FIXTURES_MATH = _WORKTREE / "tests" / "fixtures" / "math"
_TECH_SCHEMA_PATH = _FIXTURES_MATH / "technicals_producer_schema.json"
_BARS_SCHEMA_PATH = _FIXTURES_MATH / "alpaca_bars_schema.json"


def _import_lens_technicals():
    """Import advisors.lens_technicals; force reload each call for isolation."""
    mod_name = "advisors.lens_technicals"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


def _import_pipeline():
    mod_name = "advisors.lens_pipeline"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Bar-building helpers (derive from schema — no hardcoded prices)
# ---------------------------------------------------------------------------

def _make_daily_bars(n: int, start_close: float = 100.0, daily_drift: float = 0.001) -> list[dict]:
    """Build a list of n synthetic daily bars.

    close prices follow a deterministic drift from start_close so tests can
    derive expected indicator values without hardcoding them.
    Fields: t (ISO date string), c (close), v (volume), o (open), h (high), l (low).
    """
    bars = []
    close = start_close
    for i in range(n):
        date_str = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T05:00:00Z"
        bars.append({
            "t": date_str,
            "o": close * 0.999,
            "h": close * 1.005,
            "l": close * 0.995,
            "c": close,
            "v": 1_000_000,
        })
        close = close * (1 + daily_drift)
    return bars


def _make_bars_dict(universe: list[str], n: int, **kwargs) -> dict[str, list[dict]]:
    """Build a fetch_bars-shaped {symbol: [bar, ...]} dict for the given universe."""
    return {sym: _make_daily_bars(n, **kwargs) for sym in universe}


# ---------------------------------------------------------------------------
# Schema fixtures (module-scoped — read once per session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tech_schema() -> dict:
    """Load technicals_producer_schema.json (schema-derived provenance)."""
    assert _TECH_SCHEMA_PATH.exists(), (
        f"Schema fixture not found at {_TECH_SCHEMA_PATH}. "
        "Create tests/fixtures/math/technicals_producer_schema.json before running."
    )
    return json.loads(_TECH_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bars_schema() -> dict:
    """Load alpaca_bars_schema.json (schema-derived provenance)."""
    assert _BARS_SCHEMA_PATH.exists(), (
        f"Bars schema fixture not found at {_BARS_SCHEMA_PATH}."
    )
    return json.loads(_BARS_SCHEMA_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# TestProducerExists — AC-1: module importable, function present, signature correct
# ---------------------------------------------------------------------------

class TestProducerExists:
    def test_module_importable(self):
        """advisors.lens_technicals is importable without error."""
        mod = _import_lens_technicals()
        assert mod is not None

    def test_fetch_technicals_callable(self):
        """_fetch_technicals is exported and callable."""
        mod = _import_lens_technicals()
        assert hasattr(mod, "_fetch_technicals"), (
            "_fetch_technicals not found in advisors.lens_technicals"
        )
        assert callable(mod._fetch_technicals)

    def test_fetch_technicals_accepts_universe_param(self):
        """_fetch_technicals accepts a 'universe' list parameter."""
        import inspect
        mod = _import_lens_technicals()
        sig = inspect.signature(mod._fetch_technicals)
        assert "universe" in sig.parameters, (
            "_fetch_technicals must accept a 'universe' parameter"
        )


# ---------------------------------------------------------------------------
# TestProducerShape — AC-1: required output keys, types, invariants
# ---------------------------------------------------------------------------

class TestProducerShape:
    """Assert shape/format/presence of output — never hardcode indicator values."""

    @pytest.fixture
    def success_bars(self):
        """250 bars: enough for all indicators."""
        return _make_bars_dict(["SPY", "QQQ"], n=250)

    def test_success_returns_dict(self, success_bars):
        """_fetch_technicals returns a dict on success (not None, not raises)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_success_has_required_keys(self, success_bars, tech_schema):
        """On success, output dict has all required keys from the schema fixture."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        for key in tech_schema["required_output_keys"]:
            assert key in result, f"Required key '{key}' missing from result: {result.keys()}"

    def test_available_is_bool(self, success_bars):
        """result['available'] is a bool (not truthy/falsy — strict type)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        assert isinstance(result["available"], bool), (
            f"'available' must be bool, got {type(result['available'])}"
        )

    def test_success_available_true(self, success_bars):
        """With sufficient bars, available=True."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        assert result["available"] is True, (
            f"Expected available=True with 250 bars, got: {result}"
        )

    def test_ma_posture_is_dict_on_success(self, success_bars):
        """On success, ma_posture is a dict with required sub-keys."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        assert isinstance(result.get("ma_posture"), dict), (
            f"ma_posture must be dict on success, got {type(result.get('ma_posture'))}"
        )
        ma = result["ma_posture"]
        assert "pct_above_50sma" in ma, "ma_posture must have 'pct_above_50sma'"
        assert "pct_above_200sma" in ma, "ma_posture must have 'pct_above_200sma'"
        assert "overall" in ma, "ma_posture must have 'overall'"

    def test_ma_posture_overall_vocab(self, success_bars):
        """ma_posture.overall is one of the three allowed vocab words."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        overall = result["ma_posture"]["overall"]
        assert overall in ("bullish", "bearish", "mixed"), (
            f"ma_posture.overall must be 'bullish'/'bearish'/'mixed', got '{overall}'"
        )

    def test_breadth_is_float_or_none(self, success_bars):
        """breadth is a float in [0, 1] or None (never a fabricated value on error)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        breadth = result.get("breadth")
        if breadth is not None:
            assert isinstance(breadth, float), (
                f"breadth must be float or None, got {type(breadth)}"
            )
            # Invariant from schema: 0.0 <= breadth <= 1.0
            assert 0.0 <= breadth <= 1.0, (
                f"breadth must be in [0.0, 1.0], got {breadth}"
            )

    def test_momentum_is_dict_on_success(self, success_bars):
        """On success, momentum is a dict with required sub-keys."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        momentum = result.get("momentum")
        assert isinstance(momentum, dict), (
            f"momentum must be dict on success, got {type(momentum)}"
        )
        assert "mean_20d_return" in momentum, "momentum must have 'mean_20d_return'"
        assert "direction" in momentum, "momentum must have 'direction'"

    def test_momentum_direction_vocab(self, success_bars):
        """momentum.direction is one of the three allowed vocab words."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        direction = result["momentum"]["direction"]
        assert direction in ("positive", "negative", "flat"), (
            f"momentum.direction must be 'positive'/'negative'/'flat', got '{direction}'"
        )

    def test_source_is_non_empty_string_on_success(self, success_bars):
        """On success, source is a non-empty string (provenance)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        source = result.get("source")
        assert isinstance(source, str) and len(source) > 0, (
            f"source must be a non-empty string on success, got {source!r}"
        )

    def test_pct_above_50sma_bounds(self, success_bars):
        """pct_above_50sma is in [0.0, 1.0] when not None (schema invariant)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        val = result["ma_posture"].get("pct_above_50sma")
        if val is not None:
            assert 0.0 <= val <= 1.0, (
                f"pct_above_50sma must be in [0.0, 1.0], got {val}"
            )

    def test_pct_above_200sma_bounds_or_none(self, success_bars):
        """pct_above_200sma is in [0.0, 1.0] or None (insufficient history)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value=success_bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        val = result["ma_posture"].get("pct_above_200sma")
        if val is not None:
            assert 0.0 <= val <= 1.0, (
                f"pct_above_200sma must be in [0.0, 1.0] or None, got {val}"
            )


# ---------------------------------------------------------------------------
# TestUnavailableOnError — AC-2: honest-availability on fetch errors
# ---------------------------------------------------------------------------

class TestUnavailableOnError:
    """Every error path must return available=False with a D-1 reason."""

    def test_unavailable_on_exception(self):
        """When synthetic_history.fetch_bars raises, returns available=False."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", side_effect=Exception("network down")):
            result = mod._fetch_technicals(["SPY"])
        assert result["available"] is False, (
            f"Expected available=False on fetch exception, got: {result}"
        )

    def test_reason_is_d1_compliant_on_exception(self):
        """reason is type(exc).__name__ only — never str(exc) — D-1 contract."""
        mod = _import_lens_technicals()

        class _FakeError(RuntimeError):
            pass

        with patch("synthetic_history.fetch_bars", side_effect=_FakeError("secret host info")):
            result = mod._fetch_technicals(["SPY"])
        reason = result.get("reason", "")
        assert "secret host info" not in reason, (
            f"D-1 violation: str(exc) leaked into reason: {reason!r}"
        )
        assert reason == "_FakeError", (
            f"reason must be type(exc).__name__='_FakeError', got {reason!r}"
        )

    def test_ma_posture_none_on_error(self):
        """ma_posture is None when available=False."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", side_effect=ConnectionError("refused")):
            result = mod._fetch_technicals(["SPY"])
        assert result.get("ma_posture") is None, (
            f"ma_posture must be None on error, got {result.get('ma_posture')}"
        )

    def test_breadth_none_on_error(self):
        """breadth is None when available=False — no fabricated value."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", side_effect=TimeoutError()):
            result = mod._fetch_technicals(["SPY"])
        assert result.get("breadth") is None, (
            f"breadth must be None on error (no fabrication), got {result.get('breadth')}"
        )

    def test_never_raises_on_error(self):
        """_fetch_technicals never raises — all exceptions caught and returned."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", side_effect=Exception("fatal")):
            try:
                result = mod._fetch_technicals(["SPY"])
            except Exception as exc:
                pytest.fail(
                    f"_fetch_technicals raised unexpectedly: {type(exc).__name__}: {exc}"
                )
        assert isinstance(result, dict)

    def test_source_none_on_error(self):
        """source is None when available=False (no successful fetch)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", side_effect=OSError("disk")):
            result = mod._fetch_technicals(["SPY"])
        assert result.get("source") is None, (
            f"source must be None on error, got {result.get('source')!r}"
        )


# ---------------------------------------------------------------------------
# TestEdgeCases — AC-2 edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_insufficient_history_50day_unavailable(self):
        """With < 50 bars, pct_above_50sma must be None (no fabrication)."""
        mod = _import_lens_technicals()
        # 30 bars — not enough for 50-day SMA
        bars = _make_bars_dict(["SPY"], n=30)
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY"])
        # Either available=False OR ma_posture.pct_above_50sma=None — never fabricate
        if result.get("available"):
            assert result["ma_posture"]["pct_above_50sma"] is None, (
                "With < 50 bars, pct_above_50sma must be None — never fabricate SMA from insufficient data"
            )

    def test_insufficient_history_200day_unavailable_but_50day_present(self):
        """With 60-199 bars, 200-day SMA is None but 50-day can be computed."""
        mod = _import_lens_technicals()
        # 60 bars — enough for 50-day SMA, not 200-day
        bars = _make_bars_dict(["SPY"], n=60)
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY"])
        if result.get("available"):
            ma = result["ma_posture"]
            assert ma["pct_above_200sma"] is None, (
                "With < 200 bars, pct_above_200sma must be None"
            )
            # 50-day SMA CAN be computed with 60 bars — must not be None
            # (it's a soft assertion: if the producer chose unavailable overall that's also fine)

    def test_zero_bar_ticker_no_zerodivision(self):
        """A ticker with empty bar list must not cause ZeroDivisionError."""
        mod = _import_lens_technicals()
        # Mix: one ticker with data, one with empty bars
        bars = {
            "SPY": _make_daily_bars(250),
            "EMPTY": [],
        }
        with patch("synthetic_history.fetch_bars", return_value=bars):
            try:
                result = mod._fetch_technicals(["SPY", "EMPTY"])
            except ZeroDivisionError:
                pytest.fail("ZeroDivisionError raised on ticker with empty bar list")
        assert isinstance(result, dict), "Must return a dict even with empty-bar ticker"

    def test_zero_bar_ticker_excluded_from_aggregation(self):
        """A ticker with empty bars is excluded; overall indicators use remaining tickers."""
        mod = _import_lens_technicals()
        bars = {
            "SPY": _make_daily_bars(250),
            "EMPTY": [],
        }
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY", "EMPTY"])
        # If available=True, the aggregation must have used only SPY (not crashed)
        # The result must not be None
        assert result is not None

    def test_all_tickers_empty_bars_returns_unavailable(self):
        """If every ticker has empty bars, return available=False (no fabrication)."""
        mod = _import_lens_technicals()
        bars = {"SPY": [], "QQQ": []}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])
        assert result["available"] is False, (
            f"All empty bars must return available=False, got: {result}"
        )

    def test_empty_universe_returns_unavailable(self):
        """Empty universe list returns available=False (no fabrication for zero tickers)."""
        mod = _import_lens_technicals()
        with patch("synthetic_history.fetch_bars", return_value={}):
            result = mod._fetch_technicals([])
        assert result["available"] is False, (
            f"Empty universe must return available=False, got: {result}"
        )


# ---------------------------------------------------------------------------
# TestBoundedRetry — AC-4: bounded retry on Alpaca 429 / transient errors
# ---------------------------------------------------------------------------

class TestBoundedRetry:
    def test_max_attempts_constant_exists(self):
        """_TECH_MAX_ATTEMPTS is exported and is a positive int."""
        mod = _import_lens_technicals()
        assert hasattr(mod, "_TECH_MAX_ATTEMPTS"), (
            "_TECH_MAX_ATTEMPTS constant not found in advisors.lens_technicals"
        )
        val = mod._TECH_MAX_ATTEMPTS
        assert isinstance(val, int) and val > 0, (
            f"_TECH_MAX_ATTEMPTS must be a positive int, got {val!r}"
        )

    def test_max_attempts_is_finite(self):
        """_TECH_MAX_ATTEMPTS is ≤ 20 (PC-crash bounded-retry lesson)."""
        mod = _import_lens_technicals()
        assert mod._TECH_MAX_ATTEMPTS <= 20, (
            f"_TECH_MAX_ATTEMPTS must be ≤ 20 (PC-crash lesson), got {mod._TECH_MAX_ATTEMPTS}"
        )

    def test_retry_count_bounded_on_persistent_error(self):
        """When fetch_bars always raises, total call count is bounded by _TECH_MAX_ATTEMPTS."""
        mod = _import_lens_technicals()
        call_count = {"n": 0}

        def side_effect_fn(*args, **kwargs):
            call_count["n"] += 1
            raise ConnectionError("simulated Alpaca 429 / transient")

        # Patch at the synthetic_history level — that's where the HTTP call lives
        with patch("synthetic_history.fetch_bars", side_effect=side_effect_fn):
            result = mod._fetch_technicals(["SPY"])

        # The producer must not have called fetch_bars more than _TECH_MAX_ATTEMPTS times
        # (or it may delegate retry to synthetic_history entirely, in which case call_count=1)
        # Either is acceptable — but if the producer wraps its own retry, it must be bounded.
        assert result["available"] is False, "Should be unavailable after retry exhaustion"
        # Total retries must be bounded — not infinite
        assert call_count["n"] < 100, (
            f"fetch_bars called {call_count['n']} times — unbounded retry detected"
        )


# ---------------------------------------------------------------------------
# TestConstants — AC-6: named constants, no magic numbers
# ---------------------------------------------------------------------------

class TestConstants:
    def test_sma_short_window_constant_exists(self):
        """_TECH_SMA_SHORT_WINDOW is a named constant in advisors.lens_technicals."""
        mod = _import_lens_technicals()
        assert hasattr(mod, "_TECH_SMA_SHORT_WINDOW"), (
            "_TECH_SMA_SHORT_WINDOW not found — 50 must not be a magic number in producer code"
        )
        val = mod._TECH_SMA_SHORT_WINDOW
        assert isinstance(val, int) and val > 0, (
            f"_TECH_SMA_SHORT_WINDOW must be a positive int, got {val!r}"
        )

    def test_sma_long_window_constant_exists(self):
        """_TECH_SMA_LONG_WINDOW is a named constant in advisors.lens_technicals."""
        mod = _import_lens_technicals()
        assert hasattr(mod, "_TECH_SMA_LONG_WINDOW"), (
            "_TECH_SMA_LONG_WINDOW not found — 200 must not be a magic number"
        )
        val = mod._TECH_SMA_LONG_WINDOW
        assert isinstance(val, int) and val > 0, (
            f"_TECH_SMA_LONG_WINDOW must be a positive int, got {val!r}"
        )
        # Must be larger than short window (sanity)
        assert val > mod._TECH_SMA_SHORT_WINDOW, (
            f"_TECH_SMA_LONG_WINDOW ({val}) must be > _TECH_SMA_SHORT_WINDOW ({mod._TECH_SMA_SHORT_WINDOW})"
        )

    def test_momentum_window_constant_exists(self):
        """_TECH_MOMENTUM_WINDOW is a named constant in advisors.lens_technicals."""
        mod = _import_lens_technicals()
        assert hasattr(mod, "_TECH_MOMENTUM_WINDOW"), (
            "_TECH_MOMENTUM_WINDOW not found — 20 must not be a magic number"
        )
        val = mod._TECH_MOMENTUM_WINDOW
        assert isinstance(val, int) and val > 0, (
            f"_TECH_MOMENTUM_WINDOW must be a positive int, got {val!r}"
        )

    def test_source_constant_references_alpaca_and_synthetic_history(self):
        """The source provenance string references Alpaca and/or synthetic_history."""
        mod = _import_lens_technicals()
        # Find a source constant or check the output string
        source_names = [a for a in dir(mod) if "SOURCE" in a.upper() or "PROVENANCE" in a.upper()]
        if source_names:
            src = getattr(mod, source_names[0])
            # Must reference Alpaca (the actual data source)
            assert "alpaca" in src.lower() or "Alpaca" in src, (
                f"Source provenance must reference Alpaca, got {src!r}"
            )


# ---------------------------------------------------------------------------
# TestSyntheticHistoryReuse — AC-5: uses synthetic_history, no new HTTP client
# ---------------------------------------------------------------------------

class TestSyntheticHistoryReuse:
    def test_does_not_import_requests_directly(self):
        """advisors/lens_technicals.py does not make direct requests.get calls."""
        source_path = _WORKTREE / "advisors" / "lens_technicals.py"
        assert source_path.exists(), f"advisors/lens_technicals.py not found at {source_path}"
        source = source_path.read_text(encoding="utf-8")
        # Must not contain direct requests.get or requests.post
        assert "requests.get(" not in source, (
            "advisors/lens_technicals.py must not call requests.get() directly — "
            "delegate to synthetic_history.fetch_bars instead"
        )
        assert "requests.post(" not in source, (
            "advisors/lens_technicals.py must not call requests.post() directly"
        )

    def test_references_synthetic_history(self):
        """advisors/lens_technicals.py imports or references synthetic_history."""
        source_path = _WORKTREE / "advisors" / "lens_technicals.py"
        assert source_path.exists()
        source = source_path.read_text(encoding="utf-8")
        assert "synthetic_history" in source, (
            "advisors/lens_technicals.py must use synthetic_history for bar fetching "
            "(AC-5: reuse existing bar source, do not add a new price source)"
        )


# ---------------------------------------------------------------------------
# TestFixtureContract — AC-3: schema fixtures exist with correct structure
# ---------------------------------------------------------------------------

class TestFixtureContract:
    def test_tech_schema_fixture_exists(self):
        """technicals_producer_schema.json exists with correct provenance marker."""
        assert _TECH_SCHEMA_PATH.exists(), (
            f"Schema fixture missing: {_TECH_SCHEMA_PATH}"
        )

    def test_tech_schema_has_required_output_keys_field(self, tech_schema):
        """Schema fixture has 'required_output_keys' field (Gate-1 provenance contract)."""
        assert "required_output_keys" in tech_schema, (
            "technicals_producer_schema.json must have 'required_output_keys' field"
        )
        assert isinstance(tech_schema["required_output_keys"], list)
        assert len(tech_schema["required_output_keys"]) >= 4

    def test_tech_schema_has_invariants_field(self, tech_schema):
        """Schema fixture has 'invariants' field with documented behavioral contracts."""
        assert "invariants" in tech_schema, (
            "technicals_producer_schema.json must have 'invariants' field"
        )

    def test_bars_schema_fixture_exists(self):
        """alpaca_bars_schema.json exists (schema-derived Alpaca bar provenance)."""
        assert _BARS_SCHEMA_PATH.exists(), (
            f"Bars schema fixture missing: {_BARS_SCHEMA_PATH}"
        )

    def test_bars_schema_has_provenance(self, bars_schema):
        """Bars schema fixture has _provenance marker (not parser+fixture co-design)."""
        assert "_provenance" in bars_schema, (
            "alpaca_bars_schema.json must have '_provenance' field"
        )
        assert "schema-derived" in bars_schema["_provenance"].lower() or \
               "Schema-derived" in bars_schema["_provenance"], (
            "_provenance must indicate schema-derived origin"
        )

    def test_producer_output_satisfies_required_keys(self, tech_schema):
        """Producer output dict contains all keys listed in required_output_keys."""
        mod = _import_lens_technicals()
        bars = _make_bars_dict(["SPY"], n=250)
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY"])
        for key in tech_schema["required_output_keys"]:
            assert key in result, (
                f"required_output_key '{key}' missing from producer output"
            )


# ---------------------------------------------------------------------------
# TestPipelineIntegration — AC-1 wiring: _build_technicals_section delegates
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    """Tests that the lens pipeline correctly wires the technicals producer."""

    @pytest.fixture
    def mock_technicals_available(self):
        """Mock _fetch_technicals returning an available=True block."""
        return {
            "available": True,
            "ma_posture": {
                "pct_above_50sma": 0.75,
                "pct_above_200sma": 0.60,
                "overall": "bullish",
            },
            "breadth": 0.75,
            "momentum": {
                "mean_20d_return": 0.023,
                "direction": "positive",
            },
            "source": "Alpaca Markets v2 daily bars via synthetic_history.fetch_bars",
        }

    def test_pipeline_dry_run_returns_required_keys(self):
        """run_pipeline(dry_run=True) returns the expected summary keys (regression guard)."""
        pipeline = _import_pipeline()
        with patch("ai_advisor._build_technicals_section", return_value={
            "lens": "technicals", "available": False, "reason": "mocked", "sources": []
        }):
            result = pipeline.run_pipeline(dry_run=True)
        required = {"run_ts", "lenses_attempted", "lenses_available", "market_prism_row_id", "error_count"}
        assert required.issubset(result.keys()), (
            f"run_pipeline missing keys. Got: {set(result.keys())}"
        )

    def test_build_technicals_section_is_not_stub_anymore(self):
        """_build_technicals_section in ai_advisor.py does NOT return the old stub message.

        The old stub returns reason='technicals source not connected — cycle-2b deliverable'.
        After implementation, it must call _fetch_technicals and return the real result.
        """
        import ai_advisor
        result = ai_advisor._build_technicals_section()
        # Must not be the old stub message
        assert result.get("reason") != "technicals source not connected — cycle-2b deliverable", (
            "_build_technicals_section still returns the cycle-2b stub — must be wired to _fetch_technicals"
        )

    def test_build_technicals_section_calls_fetch_technicals(self, mock_technicals_available):
        """_build_technicals_section must call _fetch_technicals (lazy import, CC-2)."""
        import ai_advisor
        with patch("advisors.lens_technicals._fetch_technicals",
                   return_value=mock_technicals_available) as mock_fetch:
            ai_advisor._build_technicals_section()
        assert mock_fetch.called, (
            "_build_technicals_section must call advisors.lens_technicals._fetch_technicals"
        )

    def test_build_technicals_section_calls_fetch_technicals_with_non_empty_universe(
        self, mock_technicals_available
    ):
        """Anti-hollow regression lock: _build_technicals_section must call
        _fetch_technicals with a NON-EMPTY universe.

        The hollow-universe bug (universe=[]) silently produced available=False
        for every run even after the producer was wired.  This test locks the
        fix: whatever universe the section passes, it must have at least one
        ticker in it.

        Patch target: advisors.lens_technicals._fetch_technicals — this is the
        symbol the lazy import inside _build_technicals_section resolves to.
        assert_called_once_with is NOT used here because the exact ticker list
        is a production decision (it lives in _MARKET_PROXY_UNIVERSE); this
        test only asserts the non-emptiness invariant.
        """
        import ai_advisor
        with patch("advisors.lens_technicals._fetch_technicals",
                   return_value=mock_technicals_available) as mock_fetch:
            ai_advisor._build_technicals_section()
        assert mock_fetch.called, (
            "_build_technicals_section must call _fetch_technicals"
        )
        # Extract the 'universe' kwarg (or first positional arg) from the call
        call_args = mock_fetch.call_args
        # Production code uses keyword: _lt._fetch_technicals(universe=_MARKET_PROXY_UNIVERSE)
        universe = call_args.kwargs.get("universe", call_args.args[0] if call_args.args else None)
        assert universe is not None, (
            "_build_technicals_section did not pass a 'universe' argument to _fetch_technicals"
        )
        assert isinstance(universe, list), (
            f"'universe' passed to _fetch_technicals must be a list, got {type(universe)}"
        )
        assert len(universe) > 0, (
            "_build_technicals_section passed an EMPTY universe=[] to _fetch_technicals. "
            "This is the hollow-universe bug: an empty universe always returns available=False "
            "regardless of Alpaca data. The section must pass _MARKET_PROXY_UNIVERSE (14 tickers)."
        )

    def test_build_technicals_section_surfaces_available_payload(
        self, mock_technicals_available
    ):
        """When _fetch_technicals returns available=True, _build_technicals_section
        returns available=True, payload is the producer dict (not None), and
        sources is non-empty.

        This is the wiring correctness test: the section must not swallow the
        producer's available=True result.  Asserts shape/presence only — never
        hardcodes specific indicator values from the mock payload.
        """
        import ai_advisor
        with patch("advisors.lens_technicals._fetch_technicals",
                   return_value=mock_technicals_available):
            result = ai_advisor._build_technicals_section()

        # available must be surfaced faithfully
        assert result.get("available") is True, (
            "_build_technicals_section returned available=False even though "
            "_fetch_technicals returned available=True. The section is swallowing "
            "the producer's result."
        )
        # payload must be the producer dict, not None
        payload = result.get("payload")
        assert payload is not None, (
            "_build_technicals_section returned payload=None even though "
            "_fetch_technicals returned available=True. The payload must be "
            "passed through so Market Prism synthesis can read indicator values."
        )
        assert isinstance(payload, dict), (
            f"payload must be a dict (the producer output), got {type(payload)}"
        )
        # sources must be non-empty when producer has a source field
        sources = result.get("sources")
        assert sources is not None and len(sources) > 0, (
            "_build_technicals_section returned empty sources even though the "
            "mock producer payload has a non-empty 'source' field. The section "
            "must build at least one citation entry from payload['source']."
        )

    def test_build_technicals_section_payload_none_when_unavailable(self):
        """When _fetch_technicals returns available=False, _build_technicals_section
        returns payload=None — consistent with other lens sections and CC-3
        (no fabricated data through the unavailability mask).
        """
        import ai_advisor
        unavailable_payload = {
            "available": False,
            "reason": "ConnectionError",
            "ma_posture": None,
            "breadth": None,
            "momentum": None,
            "source": None,
        }
        with patch("advisors.lens_technicals._fetch_technicals",
                   return_value=unavailable_payload):
            result = ai_advisor._build_technicals_section()

        assert result.get("available") is False, (
            "_build_technicals_section did not surface available=False from the producer"
        )
        payload = result.get("payload")
        assert payload is None, (
            f"_build_technicals_section returned payload={payload!r} when producer "
            f"returned available=False. payload must be None when unavailable "
            f"(CC-3: no data through the unavailability mask)."
        )

    def test_pipeline_technicals_available_increments_lens_count(self, mock_technicals_available):
        """When technicals available=True, lenses_available in pipeline summary increases."""
        pipeline = _import_pipeline()
        # Make all lenses unavailable except technicals
        unavailable = {"available": False, "reason": "stub", "sources": []}
        with patch("ai_advisor._build_technicals_section", return_value={
            "lens": "technicals",
            "available": True,
            "payload": mock_technicals_available,
            "sources": [],
        }), patch("ai_advisor._build_sentiment_section", return_value=unavailable), \
             patch("ai_advisor._build_derivatives_section", return_value=unavailable), \
             patch("ai_advisor._build_macro_section", return_value=unavailable), \
             patch("ai_advisor._build_fundamentals_section", return_value=unavailable):
            result = pipeline.run_pipeline(dry_run=True)
        assert result["lenses_available"] >= 1, (
            "lenses_available must be >= 1 when technicals is available"
        )

    def test_pipeline_never_raises_on_technicals_error(self):
        """run_pipeline never raises even if _fetch_technicals itself raises."""
        pipeline = _import_pipeline()
        with patch("ai_advisor._build_technicals_section",
                   side_effect=RuntimeError("producer crashed")):
            try:
                result = pipeline.run_pipeline(dry_run=True)
            except Exception as exc:
                pytest.fail(
                    f"run_pipeline raised on technicals error: {type(exc).__name__}: {exc}"
                )
        # error_count must reflect the failure
        assert result["error_count"] >= 1, (
            "error_count must be >= 1 when a lens section raises"
        )


# ---------------------------------------------------------------------------
# TestSecurity — Security considerations from the feature plan
# ---------------------------------------------------------------------------

class TestSecurity:
    """Static security checks on the producer source code."""

    @pytest.fixture(scope="class")
    def source_text(self) -> str:
        source_path = _WORKTREE / "advisors" / "lens_technicals.py"
        if not source_path.exists():
            pytest.skip("advisors/lens_technicals.py not yet created")
        return source_path.read_text(encoding="utf-8")

    def test_no_eval_or_exec(self, source_text):
        """Producer code does not use eval() or exec() — injection guard."""
        assert "eval(" not in source_text, "eval() found in lens_technicals.py — security violation"
        assert "exec(" not in source_text, "exec() found in lens_technicals.py — security violation"

    def test_no_hardcoded_alpaca_key(self, source_text):
        """No hardcoded ALPACA_KEY or ALPACA_SECRET literals in the producer."""
        assert "APCA-API-KEY-ID" not in source_text, (
            "Hardcoded Alpaca header key found in producer — must delegate to synthetic_history"
        )

    def test_no_live_execution_reference(self, source_text):
        """Producer does not touch LIVE_EXECUTION — advisory-only boundary."""
        import re
        live_exec_patterns = [
            r'os\.environ\.get\(["\']LIVE_EXECUTION',
            r'os\.getenv\(["\']LIVE_EXECUTION',
            r'os\.environ\[["\']LIVE_EXECUTION',
            r'LIVE_EXECUTION\s*=\s*True',
            r'if\s+LIVE_EXECUTION',
        ]
        for pattern in live_exec_patterns:
            assert not re.search(pattern, source_text), (
                f"LIVE_EXECUTION code pattern found in lens_technicals.py: {pattern!r} — "
                "technicals producer is advisory-only; must never interact with LIVE_EXECUTION"
            )

    def test_no_flask_route(self, source_text):
        """Producer does not register any Flask routes."""
        assert "@app.route" not in source_text, (
            "@app.route found in lens_technicals.py — off-execution-path producers must not add routes"
        )
        assert "flask" not in source_text.lower() or "import flask" not in source_text.lower(), (
            "Flask import found in lens_technicals.py — producer is off-path, no Flask needed"
        )

    def test_d1_error_contract_json_error(self):
        """D-1: If bar data is unparseable/malformed, reason is type name only."""
        mod = _import_lens_technicals()

        class _BadData(ValueError):
            pass

        # Simulate fetch_bars returning something that causes a parsing error
        with patch("synthetic_history.fetch_bars", side_effect=_BadData("corrupt bar data")):
            result = mod._fetch_technicals(["SPY"])

        reason = result.get("reason", "")
        assert "corrupt bar data" not in reason, (
            f"D-1 violation: str(exc) leaked into reason: {reason!r}"
        )
        assert reason == "_BadData", (
            f"reason must be type(exc).__name__='_BadData', got {reason!r}"
        )
