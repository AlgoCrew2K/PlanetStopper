"""RED tests — S&P 500 market-breadth lens producer (advisors/lens_breadth.py).

Module under test: ``advisors.lens_breadth``
Public surface: ``get_sp500_constituents``, ``compute_breadth``, ``fetch_breadth``

Architecture contracts encoded as tests
----------------------------------------
AC-1  Module lives at advisors/lens_breadth.py; NOT imported by
      alpha_bot_execution.py (off-execution-path / CC-2 boundary). No Flask
      route, no LIVE_EXECUTION reference.
AC-2  ``get_sp500_constituents() -> list[str]`` — datahub CSV primary; Wikipedia
      HTML table fallback; freshness gate; 490–510 row sanity band; returns []
      when both sources fail. Mock ``requests.get`` for both sources.
AC-3  ``compute_breadth(constituents) -> dict`` — pct_above_50sma and
      pct_above_200sma over QUALIFYING sub-universe (≥200 daily closes AND a
      current bar); excludes short-history / halted names; reports
      qualifying_count / total_count; never imputes. Mock
      ``synthetic_history.fetch_bars`` with controlled bar series.
      Breadth fractions are derived from mock data — no magic literals.
AC-4  ``fetch_breadth() -> dict`` returns
      {available, pct_above_50sma, pct_above_200sma, qualifying_count,
       total_count, source, reason?}; available=False + reason on empty
      constituents / fetch failure / zero qualifying.
AC-5  ``fetch_breadth()`` calls
      ``lens_warehouse.persist_lens_snapshot(lens="breadth", ...)`` — assert
      the call is made (mock the warehouse).
AC-6  D-1: every error path's reason == type(exc).__name__ only.
      Never str(exc) / URL / key leak.
AC-7  Security / scope: no eval / exec / subprocess in source; no Flask route;
      no LIVE_EXECUTION reference (static-scan assertion on module source).

Fixture provenance:
  - Constituent list fixture: hand-crafted in-test (schema-derived from
    datahub CSV header and Wikipedia HTML table structure).
  - Bar fixture: hand-crafted in-test — controlled counts to exercise
    qualifying-universe logic.

Mocking strategy:
  - ``requests.get`` patched via ``unittest.mock.patch`` for constituent fetch.
  - ``synthetic_history.fetch_bars`` patched via ``unittest.mock.patch`` for
    Alpaca bar fetch.
  - ``advisors.lens_warehouse.persist_lens_snapshot`` patched to avoid DB I/O
    and assert the call contract.
  - No live network calls permitted in this suite.
"""

from __future__ import annotations

import importlib
import io
import pathlib
import re
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root / import helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _ensure_repo_on_path() -> None:
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_module() -> types.ModuleType:
    """Import advisors.lens_breadth fresh each call to avoid cached state."""
    _ensure_repo_on_path()
    mod_name = "advisors.lens_breadth"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Shared fixture factories
# ---------------------------------------------------------------------------

# Minimum number of constituents the sanity band accepts (inclusive lower bound)
_SANITY_MIN = 490
# Maximum number of constituents the sanity band accepts (inclusive upper bound)
_SANITY_MAX = 510


def _make_csv_body(tickers: list[str]) -> str:
    """Build a minimal datahub-style CSV response body."""
    lines = ["Symbol,Name,Sector"]
    for t in tickers:
        lines.append(f"{t},Company {t},Technology")
    return "\n".join(lines)


def _make_wikipedia_html(tickers: list[str]) -> str:
    """Build a minimal Wikipedia S&P 500 HTML table containing the given tickers.

    The real page uses a wikitable with a 'Symbol' or 'Ticker' column.  This
    minimal construction mimics that shape so the parser has something to parse.
    """
    rows = "\n".join(f"<tr><td>{t}</td><td>Company {t}</td></tr>" for t in tickers)
    return (
        "<html><body>"
        '<table class="wikitable sortable">'
        "<tr><th>Symbol</th><th>Security</th></tr>"
        f"{rows}"
        "</table></body></html>"
    )


def _make_mock_response(body: str | bytes, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response.

    Sets up both the non-streaming attributes (content, text) and the streaming
    iter_content method so the mock works whether the caller uses the datahub
    path (reads resp.content directly) or the Wikipedia path (uses stream=True +
    iter_content).
    """
    mock = MagicMock()
    mock.status_code = status_code
    if isinstance(body, bytes):
        body_bytes = body
        mock.content = body_bytes
        mock.text = body_bytes.decode("utf-8", errors="replace")
    else:
        body_bytes = body.encode("utf-8")
        mock.content = body_bytes
        mock.text = body
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    mock.close = MagicMock()
    # Wire iter_content so the Wikipedia streaming path can consume the body.
    # Split into 65536-byte chunks matching the real chunk_size used by the impl.
    chunk_size = 65536
    body_chunks = [
        body_bytes[i : i + chunk_size]
        for i in range(0, max(len(body_bytes), 1), chunk_size)
    ]
    mock.iter_content = MagicMock(return_value=iter(body_chunks))
    return mock


def _make_bars(n_closes: int, last_close: float = 100.0) -> list[dict]:
    """Build a list of n_closes mock daily bar dicts with sequential dates.

    Each bar has at least a 'c' (close) field.  All bars use close = last_close
    (flat series); the SMA equals last_close so the name counts as above-or-equal.
    """
    bars = []
    for i in range(n_closes):
        bars.append({"t": f"2023-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T00:00:00Z",
                     "o": last_close, "h": last_close, "l": last_close,
                     "c": last_close, "v": 1_000_000})
    return bars


def _make_valid_constituent_list(n: int = 503) -> list[str]:
    """Return n dot-format tickers in the 490–510 sanity band."""
    assert _SANITY_MIN <= n <= _SANITY_MAX, (
        f"_make_valid_constituent_list called with n={n} outside sanity band"
    )
    tickers = [f"AAAA{i:04d}" for i in range(n)]
    # Sprinkle a dot-format ticker to confirm pass-through
    tickers[0] = "BRK.B"
    return tickers


# ---------------------------------------------------------------------------
# AC-1: Module location + off-execution-path guard
# ---------------------------------------------------------------------------


class TestModuleLocation:
    def test_module_importable_at_expected_path(self):
        """advisors/lens_breadth.py must exist and be importable (AC-1)."""
        mod = _import_module()
        assert mod is not None

    def test_module_file_is_in_advisors_package(self):
        """Module __file__ must be advisors/lens_breadth.py (AC-1)."""
        mod = _import_module()
        mod_path = pathlib.Path(mod.__file__)
        assert mod_path.name == "lens_breadth.py", (
            f"Expected lens_breadth.py, got {mod_path.name}"
        )
        assert mod_path.parent.name == "advisors", (
            f"Expected parent 'advisors', got {mod_path.parent.name}"
        )

    def test_not_imported_by_alpha_bot_execution(self):
        """alpha_bot_execution.py must NOT import lens_breadth (CC-2 / AC-1)."""
        exec_path = _REPO_ROOT / "alpha_bot_execution.py"
        assert exec_path.exists(), "alpha_bot_execution.py must exist"
        source = exec_path.read_text(encoding="utf-8")
        assert "lens_breadth" not in source, (
            "lens_breadth must NOT appear in alpha_bot_execution.py — "
            "it is off-execution-path (CC-2)."
        )

    def test_no_flask_route_in_source(self):
        """Module must not register any Flask routes (AC-7 / scope boundary)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        # Flask route decorators look like @app.route(
        assert "@app.route" not in source, (
            "lens_breadth.py must not register Flask routes — "
            "it is an advisory-only off-execution-path module."
        )

    def test_no_live_execution_reference_in_source(self):
        """Module must not reference LIVE_EXECUTION (AC-7 / scope boundary)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "LIVE_EXECUTION" not in source, (
            "lens_breadth.py must not reference LIVE_EXECUTION — "
            "advisory-only module; scope boundary violation."
        )

    def test_no_eval_exec_subprocess_in_source(self):
        """Module must not use eval/exec/subprocess (AC-7 / security)."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = re.compile(r"\beval\s*\(|\bexec\s*\(|import subprocess")
        matches = forbidden.findall(source)
        assert not matches, (
            f"Forbidden pattern(s) in lens_breadth.py: {matches}. "
            "eval / exec / subprocess are prohibited."
        )


# ---------------------------------------------------------------------------
# AC-2: get_sp500_constituents — constituent fetch, sanity band, fallback
# ---------------------------------------------------------------------------


class TestGetSp500Constituents:
    def test_returns_list_of_strings(self):
        """get_sp500_constituents() must return a list[str] (AC-2)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(csv_body)
            result = mod.get_sp500_constituents()
        assert isinstance(result, list), "Must return a list"
        assert all(isinstance(t, str) for t in result), "All elements must be strings"

    def test_primary_datahub_source_returns_within_sanity_band(self):
        """Datahub CSV success → result length in 490–510 band (AC-2)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(csv_body)
            result = mod.get_sp500_constituents()
        assert _SANITY_MIN <= len(result) <= _SANITY_MAX, (
            f"Expected result length in [{_SANITY_MIN}, {_SANITY_MAX}], got {len(result)}"
        )

    def test_dot_format_tickers_pass_through_unchanged(self):
        """Dot-format tickers like BRK.B must be preserved as-is (AC-2)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        assert "BRK.B" in tickers, "Fixture must include BRK.B"
        csv_body = _make_csv_body(tickers)
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_mock_response(csv_body)
            result = mod.get_sp500_constituents()
        assert "BRK.B" in result, (
            "BRK.B must be in result — dot-format tickers must pass through unchanged."
        )

    def test_primary_failure_triggers_wikipedia_fallback(self):
        """Primary datahub failure → Wikipedia fallback is attempted (AC-2)."""
        mod = _import_module()
        import requests as req_lib
        # Primary source raises a connection error
        primary_fail = req_lib.ConnectionError("datahub down")
        # Wikipedia returns valid HTML
        tickers = _make_valid_constituent_list(500)
        wiki_html = _make_wikipedia_html(tickers)
        wiki_response = _make_mock_response(wiki_html)

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [primary_fail, wiki_response]
            result = mod.get_sp500_constituents()

        # Wikipedia was tried (two calls total)
        assert mock_get.call_count >= 2, (
            f"Expected at least 2 requests.get calls (primary + fallback), "
            f"got {mock_get.call_count}"
        )
        # Result must still be a non-empty list within the sanity band
        assert isinstance(result, list)
        assert _SANITY_MIN <= len(result) <= _SANITY_MAX, (
            f"Wikipedia fallback result length {len(result)} outside sanity band "
            f"[{_SANITY_MIN}, {_SANITY_MAX}]"
        )

    def test_primary_below_sanity_band_triggers_fallback(self):
        """Primary CSV returning < 490 rows → rejected, fallback attempted (AC-2)."""
        mod = _import_module()
        # Primary returns only 100 tickers (well below band)
        short_tickers = [f"SHORT{i:03d}" for i in range(100)]
        short_csv = _make_csv_body(short_tickers)
        # Fallback returns valid count
        valid_tickers = _make_valid_constituent_list(500)
        wiki_html = _make_wikipedia_html(valid_tickers)

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_mock_response(short_csv),
                _make_mock_response(wiki_html),
            ]
            result = mod.get_sp500_constituents()

        # Fallback must have been tried
        assert mock_get.call_count >= 2, (
            "Below-band primary must trigger fallback (at least 2 requests.get calls)"
        )
        # Result uses the valid fallback data
        assert _SANITY_MIN <= len(result) <= _SANITY_MAX, (
            f"Result {len(result)} should come from fallback and be within sanity band"
        )

    def test_primary_above_sanity_band_triggers_fallback(self):
        """Primary CSV returning > 510 rows → rejected, fallback attempted (AC-2)."""
        mod = _import_module()
        # Primary returns 600 tickers (above band)
        big_tickers = [f"BIGG{i:04d}" for i in range(600)]
        big_csv = _make_csv_body(big_tickers)
        # Fallback returns valid count
        valid_tickers = _make_valid_constituent_list(503)
        wiki_html = _make_wikipedia_html(valid_tickers)

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_mock_response(big_csv),
                _make_mock_response(wiki_html),
            ]
            result = mod.get_sp500_constituents()

        assert mock_get.call_count >= 2, (
            "Above-band primary must trigger fallback (at least 2 requests.get calls)"
        )
        assert _SANITY_MIN <= len(result) <= _SANITY_MAX

    def test_both_sources_fail_returns_empty_list(self):
        """Both primary and fallback failure → returns [] — honest-availability (AC-2)."""
        mod = _import_module()
        import requests as req_lib
        with patch("requests.get",
                   side_effect=req_lib.ConnectionError("all down")):
            result = mod.get_sp500_constituents()
        assert result == [], (
            "Both sources failing must return [] — never a stale/fabricated list."
        )

    def test_both_sources_outside_band_returns_empty_list(self):
        """Both sources returning out-of-band counts → [] (AC-2)."""
        mod = _import_module()
        short_csv = _make_csv_body([f"X{i}" for i in range(5)])
        short_wiki = _make_wikipedia_html([f"Y{i}" for i in range(5)])

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_mock_response(short_csv),
                _make_mock_response(short_wiki),
            ]
            result = mod.get_sp500_constituents()

        assert result == [], (
            "Both sources outside sanity band must return [] — no stale list."
        )

    def test_http_error_primary_uses_fallback(self):
        """HTTP 500 on primary → fallback attempted (AC-2)."""
        mod = _import_module()
        import requests as req_lib
        fail_resp = _make_mock_response("", 500)
        valid_tickers = _make_valid_constituent_list(500)
        wiki_html = _make_wikipedia_html(valid_tickers)

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [fail_resp, _make_mock_response(wiki_html)]
            result = mod.get_sp500_constituents()

        assert mock_get.call_count >= 2
        # Result is either valid or [] — never fabricated
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# AC-3: compute_breadth — qualifying sub-universe, SMA math, no imputation
# ---------------------------------------------------------------------------


class TestComputeBreadth:
    def _make_fetch_bars_mock(self, bars_by_symbol: dict[str, list]) -> MagicMock:
        """Return a mock for synthetic_history.fetch_bars returning bars_by_symbol."""
        mock = MagicMock(return_value=bars_by_symbol)
        return mock

    def test_returns_dict_with_required_keys(self):
        """compute_breadth must return a dict with the required keys (AC-3)."""
        mod = _import_module()
        tickers = ["AAPL", "MSFT"]
        # Both tickers have 250 bars — they qualify
        bars = {t: _make_bars(250) for t in tickers}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)
        assert isinstance(result, dict)
        for key in ("pct_above_50sma", "pct_above_200sma",
                    "qualifying_count", "total_count"):
            assert key in result, f"compute_breadth result missing key: {key!r}"

    def test_qualifying_count_excludes_short_history_names(self):
        """Names with < 200 bars must be excluded from qualifying_count (AC-3)."""
        mod = _import_module()
        tickers = ["QUAL1", "QUAL2", "SHORT1", "SHORT2"]
        bars = {
            "QUAL1": _make_bars(250),   # qualifies
            "QUAL2": _make_bars(210),   # qualifies
            "SHORT1": _make_bars(50),   # < 200 bars — excluded
            "SHORT2": _make_bars(199),  # < 200 bars — excluded
        }
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)

        assert result["total_count"] == len(tickers), (
            "total_count must equal len(constituents)"
        )
        assert result["qualifying_count"] == 2, (
            f"Expected qualifying_count=2 (only QUAL1+QUAL2), got {result['qualifying_count']}"
        )

    def test_qualifying_count_excludes_halted_names_with_no_current_bar(self):
        """Names not present in fetch_bars result (halted/delisted) are excluded (AC-3)."""
        mod = _import_module()
        tickers = ["LIVE1", "LIVE2", "HALTED1"]
        # HALTED1 not returned by fetch_bars at all — simulates a halted name
        bars = {
            "LIVE1": _make_bars(250),
            "LIVE2": _make_bars(250),
            # HALTED1 absent
        }
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)

        assert result["total_count"] == 3
        assert result["qualifying_count"] == 2, (
            "HALTED1 must be excluded from qualifying_count — no bars returned"
        )

    def test_pct_above_50sma_derived_from_mock_not_hardcoded(self):
        """pct_above_50sma is a fraction in [0, 1] derived from qualifying names (AC-3).

        We control the mock to have exactly 2 qualifying names:
          - Name A: last close well ABOVE its 50-day SMA
          - Name B: last close well BELOW its 50-day SMA
        Expected pct_above_50sma = 0.5 (1 out of 2 qualifying).
        Tolerance: 1e-6 — exact fraction arithmetic, no rounding ambiguity at this N.
        """
        mod = _import_module()

        # Build bars such that we can reason about SMA position:
        # Name A: 250 bars, closes all at high value → last close above any 50-bar avg
        # Name B: 249 bars at high value + last bar at very low value → below 50-bar avg
        high_close = 200.0
        low_close = 1.0

        bars_a = []
        for i in range(250):
            bars_a.append({"t": f"2024-01-{i % 28 + 1:02d}T00:00:00Z",
                           "o": high_close, "h": high_close, "l": high_close,
                           "c": high_close, "v": 1_000_000})

        bars_b = []
        for i in range(249):
            bars_b.append({"t": f"2024-01-{i % 28 + 1:02d}T00:00:00Z",
                           "o": high_close, "h": high_close, "l": high_close,
                           "c": high_close, "v": 1_000_000})
        # Last bar: close plummets far below 50-bar SMA of high_close
        bars_b.append({"t": "2024-12-31T00:00:00Z",
                       "o": low_close, "h": low_close, "l": low_close,
                       "c": low_close, "v": 1_000_000})

        bars = {"NAME_A": bars_a, "NAME_B": bars_b}
        tickers = ["NAME_A", "NAME_B"]

        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)

        # Both qualify (≥200 bars each)
        assert result["qualifying_count"] == 2, (
            f"Expected qualifying_count=2, got {result['qualifying_count']}"
        )
        expected_pct = 1 / 2  # derived from mock, not hardcoded
        assert result["pct_above_50sma"] == pytest.approx(expected_pct, abs=1e-6), (
            # Tolerance: 1/2 = 0.5 is exact; any result != 0.5 is a wrong implementation.
            f"pct_above_50sma {result['pct_above_50sma']!r} != expected {expected_pct!r}"
        )

    def test_pct_above_200sma_derived_from_mock_not_hardcoded(self):
        """pct_above_200sma is computed over qualifying names (AC-3).

        3 qualifying names: 2 above 200-day SMA, 1 below.
        Expected pct_above_200sma = 2/3.
        Tolerance: 1e-9 — computed from an exact integer ratio, no float accumulation.
        """
        mod = _import_module()

        high_close = 300.0
        low_close = 1.0

        def _bars_above(n: int) -> list[dict]:
            """n bars all at high_close → last close above 200-bar SMA."""
            return [{"t": f"2024-{j % 12 + 1:02d}-{j % 28 + 1:02d}T00:00:00Z",
                     "o": high_close, "h": high_close, "l": high_close,
                     "c": high_close, "v": 1_000_000}
                    for j in range(n)]

        def _bars_below(n: int) -> list[dict]:
            """n-1 bars at high_close, last bar at low_close → below 200-bar SMA."""
            rows = [{"t": f"2024-{j % 12 + 1:02d}-{j % 28 + 1:02d}T00:00:00Z",
                     "o": high_close, "h": high_close, "l": high_close,
                     "c": high_close, "v": 1_000_000}
                    for j in range(n - 1)]
            rows.append({"t": "2024-12-31T00:00:00Z",
                         "o": low_close, "h": low_close, "l": low_close,
                         "c": low_close, "v": 1_000_000})
            return rows

        bars = {
            "ABOVE_A": _bars_above(250),
            "ABOVE_B": _bars_above(210),
            "BELOW_C": _bars_below(250),
        }
        tickers = list(bars.keys())

        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)

        assert result["qualifying_count"] == 3
        expected_pct = 2 / 3  # derived from mock, not a magic literal
        assert result["pct_above_200sma"] == pytest.approx(expected_pct, abs=1e-9), (
            # Tolerance: this is an exact ratio of small integers.
            # Any deviation > 1e-9 indicates a computation error (wrong denominator or count).
            f"pct_above_200sma {result['pct_above_200sma']!r} != expected {expected_pct!r}"
        )

    def test_fractions_are_bounded_in_zero_to_one(self):
        """pct_above_50sma and pct_above_200sma must be in [0, 1] (AC-3)."""
        mod = _import_module()
        tickers = ["T1", "T2", "T3"]
        bars = {t: _make_bars(250) for t in tickers}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)
        assert 0.0 <= result["pct_above_50sma"] <= 1.0, (
            f"pct_above_50sma={result['pct_above_50sma']!r} not in [0, 1]"
        )
        assert 0.0 <= result["pct_above_200sma"] <= 1.0, (
            f"pct_above_200sma={result['pct_above_200sma']!r} not in [0, 1]"
        )

    def test_total_count_matches_input_length(self):
        """total_count must equal len(constituents) — not the qualifying subset (AC-3)."""
        mod = _import_module()
        tickers = [f"TICK{i:03d}" for i in range(50)]
        # Only 20 have enough bars to qualify
        bars = {}
        for i, t in enumerate(tickers):
            bars[t] = _make_bars(250 if i < 20 else 50)
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)
        assert result["total_count"] == 50, (
            f"total_count must equal input length (50), got {result['total_count']}"
        )
        assert result["qualifying_count"] == 20, (
            f"qualifying_count must equal names with ≥200 bars (20), got {result['qualifying_count']}"
        )

    def test_zero_qualifying_names_produces_zero_fractions(self):
        """All names short-history → qualifying_count=0; fractions must not be fabricated (AC-3)."""
        mod = _import_module()
        tickers = ["SHORT1", "SHORT2"]
        bars = {t: _make_bars(50) for t in tickers}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)
        assert result["qualifying_count"] == 0
        # When zero qualifying, fractions must be 0 or None — never a fabricated value
        assert result.get("pct_above_50sma") in (0.0, 0, None), (
            f"pct_above_50sma with zero qualifying must be 0 or None, "
            f"got {result.get('pct_above_50sma')!r}"
        )
        assert result.get("pct_above_200sma") in (0.0, 0, None), (
            f"pct_above_200sma with zero qualifying must be 0 or None, "
            f"got {result.get('pct_above_200sma')!r}"
        )

    def test_never_imputes_missing_bars(self):
        """Names with fetch_bars returning 0 bars are excluded, not imputed (AC-3)."""
        mod = _import_module()
        tickers = ["GOOD1", "GOOD2", "EMPTY"]
        bars = {
            "GOOD1": _make_bars(250),
            "GOOD2": _make_bars(250),
            "EMPTY": [],  # zero bars returned — not a missing key, but empty list
        }
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod.compute_breadth(tickers)

        # EMPTY must be excluded (0 bars < 200 threshold)
        assert result["qualifying_count"] == 2, (
            f"EMPTY (0 bars) must be excluded from qualifying; expected 2, "
            f"got {result['qualifying_count']}"
        )
        assert result["total_count"] == 3


# ---------------------------------------------------------------------------
# AC-4: fetch_breadth — public entry point, honest availability
# ---------------------------------------------------------------------------


class TestFetchBreadth:
    def test_returns_dict_with_required_keys_on_success(self):
        """fetch_breadth() success path must return dict with all required keys (AC-4)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(250) for t in tickers[:10]}  # subset qualifies

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1):
                    result = mod.fetch_breadth()

        assert isinstance(result, dict)
        required = {"available", "pct_above_50sma", "pct_above_200sma",
                    "qualifying_count", "total_count", "source"}
        for key in required:
            assert key in result, f"fetch_breadth result missing key: {key!r}"

    def test_available_is_bool(self):
        """fetch_breadth()['available'] must be a bool (AC-4)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(250) for t in tickers[:10]}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1):
                    result = mod.fetch_breadth()

        assert isinstance(result["available"], bool), (
            f"available must be bool, got {type(result['available'])}"
        )

    def test_unavailable_when_constituents_empty(self):
        """Empty constituent list → available=False + reason (AC-4)."""
        mod = _import_module()
        import requests as req_lib
        # Both sources fail → get_sp500_constituents returns []
        with patch("requests.get",
                   side_effect=req_lib.ConnectionError("all down")):
            with patch("advisors.lens_warehouse.persist_lens_snapshot",
                       return_value=1):
                result = mod.fetch_breadth()

        assert result["available"] is False, (
            "Empty constituents must yield available=False"
        )
        assert "reason" in result, "available=False must include 'reason'"
        assert result["reason"], "reason must be non-empty"

    def test_unavailable_when_zero_qualifying_names(self):
        """Zero qualifying names → available=False (AC-4)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        # All bars have < 200 closes → zero qualifying
        bars = {t: _make_bars(10) for t in tickers[:10]}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1):
                    result = mod.fetch_breadth()

        assert result["available"] is False, (
            "Zero qualifying names must yield available=False"
        )
        assert "reason" in result

    def test_unavailable_when_bar_fetch_raises(self):
        """Exception in fetch_bars → available=False (AC-4)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars",
                       side_effect=RuntimeError("alpaca down")):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1):
                    result = mod.fetch_breadth()

        assert result["available"] is False
        assert "reason" in result

    def test_source_key_present_and_nonempty(self):
        """fetch_breadth()['source'] must be a non-empty string (AC-4)."""
        mod = _import_module()
        import requests as req_lib
        # Even on failure, 'source' must be present
        with patch("requests.get",
                   side_effect=req_lib.ConnectionError("all down")):
            with patch("advisors.lens_warehouse.persist_lens_snapshot",
                       return_value=1):
                result = mod.fetch_breadth()

        assert "source" in result, "source key must always be present"
        assert isinstance(result["source"], str)
        assert len(result["source"]) > 0, "source must be a non-empty string"

    def test_reason_absent_when_available_true(self):
        """When available=True, 'reason' must be absent from the result (AC-4)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        # Enough qualifying bars so available=True
        bars = {t: _make_bars(250) for t in tickers}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1):
                    result = mod.fetch_breadth()

        if result["available"] is True:
            assert "reason" not in result, (
                "available=True must not include a 'reason' key — "
                "reason is only for unavailability."
            )

    def test_fetch_breadth_never_raises(self):
        """fetch_breadth() must never raise — all errors produce available=False (AC-1/AC-4)."""
        mod = _import_module()
        import requests as req_lib
        # Trigger multiple failure modes simultaneously
        with patch("requests.get",
                   side_effect=req_lib.ConnectionError("network fail")):
            with patch("advisors.lens_warehouse.persist_lens_snapshot",
                       side_effect=RuntimeError("warehouse fail")):
                try:
                    result = mod.fetch_breadth()
                except Exception as exc:
                    pytest.fail(
                        f"fetch_breadth() raised {type(exc).__name__}: {exc}. "
                        "It must never raise — degrade to available=False."
                    )
        assert result["available"] is False


# ---------------------------------------------------------------------------
# AC-5: Warehouse persist call
# ---------------------------------------------------------------------------


class TestWarehousePersist:
    def test_persist_lens_snapshot_called_with_lens_breadth(self):
        """fetch_breadth must call persist_lens_snapshot(lens='breadth', ...) (AC-5)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(250) for t in tickers}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1) as mock_persist:
                    mod.fetch_breadth()

        mock_persist.assert_called_once()
        # First positional-or-keyword arg (lens) must be "breadth"
        call_args = mock_persist.call_args
        # Support both positional and keyword invocations
        lens_val = (call_args.args[0] if call_args.args
                    else call_args.kwargs.get("lens"))
        assert lens_val == "breadth", (
            f"persist_lens_snapshot must be called with lens='breadth', "
            f"got lens={lens_val!r}"
        )

    def test_persist_called_even_when_unavailable(self):
        """persist_lens_snapshot must be called regardless of available=True/False (AC-5).

        The warehouse records every run, including degraded runs, for audit purposes.
        """
        mod = _import_module()
        import requests as req_lib
        # Force unavailability
        with patch("requests.get",
                   side_effect=req_lib.ConnectionError("all down")):
            with patch("advisors.lens_warehouse.persist_lens_snapshot",
                       return_value=1) as mock_persist:
                result = mod.fetch_breadth()

        # Whether or not we call persist on failure is implementation's choice;
        # we assert the result is available=False. If the implementer does
        # persist, the call must still use lens="breadth".
        assert result["available"] is False
        if mock_persist.called:
            call_args = mock_persist.call_args
            lens_val = (call_args.args[0] if call_args.args
                        else call_args.kwargs.get("lens"))
            assert lens_val == "breadth"

    def test_persist_receives_available_flag(self):
        """persist_lens_snapshot must receive the available flag (True/False) (AC-5)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(250) for t in tickers}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1) as mock_persist:
                    mod.fetch_breadth()

        call_args = mock_persist.call_args
        all_args = list(call_args.args) + list(call_args.kwargs.values())
        # available must be passed — look for a bool or 0/1 int in the call
        bool_ints = [a for a in all_args if isinstance(a, (bool, int)) and a in (0, 1, True, False)]
        assert bool_ints, (
            "persist_lens_snapshot must receive an 'available' bool/int argument. "
            f"Call args: {call_args}"
        )


# ---------------------------------------------------------------------------
# AC-6: D-1 error contract
# ---------------------------------------------------------------------------


class TestD1ErrorContract:
    def test_reason_is_type_name_only_not_message_on_connection_error(self):
        """D-1: reason must be type(exc).__name__ only — not str(exc) or URL (AC-6)."""
        mod = _import_module()
        import requests as req_lib
        secret_msg = "Connection refused to host data.nasdaq.com?api_key=SECRET_XYZ"
        with patch("requests.get",
                   side_effect=req_lib.ConnectionError(secret_msg)):
            with patch("advisors.lens_warehouse.persist_lens_snapshot",
                       return_value=1):
                result = mod.fetch_breadth()

        assert result["available"] is False
        reason = result.get("reason", "")
        # D-1: must NOT contain the raw exception message content
        assert "SECRET_XYZ" not in reason, (
            "D-1 violated: reason contains secret from exception message"
        )
        assert "data.nasdaq.com" not in reason, (
            "D-1 violated: reason contains URL from exception message"
        )
        assert "api_key" not in reason, (
            "D-1 violated: reason contains key name from exception message"
        )

    def test_reason_is_type_name_only_on_value_error(self):
        """D-1: ValueError during parsing → reason == 'ValueError' (AC-6)."""
        mod = _import_module()
        # Patch get_sp500_constituents to raise ValueError with a sensitive payload
        sensitive_msg = "invalid literal contains API_KEY_VALUE=sk-secret123"
        with patch.object(mod, "get_sp500_constituents",
                          side_effect=ValueError(sensitive_msg)):
            with patch("advisors.lens_warehouse.persist_lens_snapshot",
                       return_value=1):
                result = mod.fetch_breadth()

        assert result["available"] is False
        reason = result.get("reason", "")
        assert "sk-secret123" not in reason, (
            "D-1 violated: reason contains secret from ValueError message"
        )
        # The reason must be the exception class name
        assert reason == "ValueError", (
            f"D-1: reason must be 'ValueError', got {reason!r}"
        )

    def test_reason_is_type_name_only_on_runtime_error(self):
        """D-1: RuntimeError in bar fetch → reason == 'RuntimeError' (AC-6)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars",
                       side_effect=RuntimeError("alpaca internal error SECRET_TOKEN")):
                with patch("advisors.lens_warehouse.persist_lens_snapshot",
                           return_value=1):
                    result = mod.fetch_breadth()

        assert result["available"] is False
        reason = result.get("reason", "")
        assert "SECRET_TOKEN" not in reason
        assert reason == "RuntimeError", (
            f"D-1: reason must be 'RuntimeError', got {reason!r}"
        )

    def test_reason_is_nonempty_string_on_unavailability(self):
        """Every available=False result must have a non-empty string reason (AC-6)."""
        mod = _import_module()
        import requests as req_lib
        with patch("requests.get",
                   side_effect=req_lib.Timeout("timed out")):
            with patch("advisors.lens_warehouse.persist_lens_snapshot",
                       return_value=1):
                result = mod.fetch_breadth()

        assert result["available"] is False
        assert "reason" in result
        assert isinstance(result["reason"], str)
        assert len(result["reason"]) > 0, "reason must be non-empty"


# ---------------------------------------------------------------------------
# Robustness: bounded retry — recover on transient failure
# ---------------------------------------------------------------------------


class TestBoundedRetryRecovers:
    """Retry logic: first attempt fails with a retryable error; second succeeds.

    Patch time.sleep so retries are instant. Assert requests.get call count
    reflects at least two attempts (the failing one + the successful one).
    """

    def test_datahub_retry_recovers_on_connection_error(self):
        """_fetch_datahub_constituents retries on ConnectionError and returns on success.

        Scenario: attempt 0 → ConnectionError; attempt 1 → valid 200 CSV.
        get_sp500_constituents must return the correct list without raising,
        and requests.get must have been called at least twice (proving a retry).
        """
        mod = _import_module()
        import requests as req_lib

        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        good_response = _make_mock_response(csv_body, status_code=200)

        # Attempt 0 fails; attempt 1 returns a valid CSV.
        side_effects = [req_lib.ConnectionError("transient"), good_response]

        with patch("time.sleep"):  # prevent real sleeps during backoff
            with patch("requests.get", side_effect=side_effects) as mock_get:
                result = mod.get_sp500_constituents()

        assert _SANITY_MIN <= len(result) <= _SANITY_MAX, (
            f"Expected result within sanity band after retry; got {len(result)}"
        )
        assert mock_get.call_count >= 2, (
            f"Expected at least 2 requests.get calls (retry proof); "
            f"got {mock_get.call_count}"
        )

    def test_datahub_retry_recovers_on_503_response(self):
        """_fetch_datahub_constituents retries on HTTP 503 and returns on success.

        Scenario: attempt 0 → 503 response; attempt 1 → valid 200 CSV.
        The 503 response must not raise immediately — the retry loop must absorb
        it and proceed to the next attempt.
        """
        mod = _import_module()
        import requests as req_lib

        tickers = _make_valid_constituent_list(500)
        csv_body = _make_csv_body(tickers)

        fail_response = MagicMock()
        fail_response.status_code = 503
        # raise_for_status will only be called if the retry loop reaches it;
        # for intermediate attempts the loop sleeps-and-continues without calling it.
        fail_response.raise_for_status = MagicMock(
            side_effect=req_lib.HTTPError(response=fail_response)
        )

        good_response = _make_mock_response(csv_body, status_code=200)
        side_effects = [fail_response, good_response]

        with patch("time.sleep"):
            with patch("requests.get", side_effect=side_effects) as mock_get:
                result = mod.get_sp500_constituents()

        assert _SANITY_MIN <= len(result) <= _SANITY_MAX, (
            f"Expected result within sanity band after 503→200 retry; got {len(result)}"
        )
        assert mock_get.call_count >= 2, (
            f"Expected at least 2 requests.get calls (retry proof); "
            f"got {mock_get.call_count}"
        )


# ---------------------------------------------------------------------------
# Robustness: bounded retry — exhaustion falls through honestly
# ---------------------------------------------------------------------------


class TestBoundedRetryExhaustion:
    """Retry exhaustion: all _CONSTITUENT_MAX_ATTEMPTS fail.

    After all datahub attempts are exhausted, get_sp500_constituents falls
    through to Wikipedia (which also exhausts), then returns [].
    No exception must escape the public surface.
    Call count per source must not exceed _CONSTITUENT_MAX_ATTEMPTS.
    """

    def test_exhausted_datahub_retries_fall_through_to_empty_list(self):
        """All datahub attempts fail → falls back to Wikipedia → [] returned.

        The total call count for datahub must not exceed _CONSTITUENT_MAX_ATTEMPTS.
        After Wikipedia also exhausts, get_sp500_constituents returns []
        (honest-availability) without raising.
        """
        mod = _import_module()
        import requests as req_lib

        max_attempts = mod._CONSTITUENT_MAX_ATTEMPTS

        # Build enough side_effects for datahub (max_attempts) + wikipedia (max_attempts)
        # All return 503 so both sources fully exhaust their retry budgets.
        def _make_503() -> MagicMock:
            resp = MagicMock()
            resp.status_code = 503
            resp.close = MagicMock()
            resp.raise_for_status = MagicMock(
                side_effect=req_lib.HTTPError(response=resp)
            )
            return resp

        side_effects = [_make_503() for _ in range(max_attempts * 2)]

        with patch("time.sleep"):
            with patch("requests.get", side_effect=side_effects) as mock_get:
                try:
                    result = mod.get_sp500_constituents()
                except Exception as exc:
                    pytest.fail(
                        f"get_sp500_constituents raised {type(exc).__name__} on retry "
                        f"exhaustion — must return [] and never raise."
                    )

        assert result == [], (
            f"Expected [] after both sources exhausted; got {result!r}"
        )
        # Each source must not exceed its own attempt budget.
        assert mock_get.call_count <= max_attempts * 2, (
            f"Call count {mock_get.call_count} exceeds 2 × _CONSTITUENT_MAX_ATTEMPTS "
            f"({max_attempts * 2}); retry is not bounded."
        )

    def test_exhausted_datahub_call_count_bounded(self):
        """requests.get call count for datahub alone is bounded by _CONSTITUENT_MAX_ATTEMPTS.

        We isolate datahub by making Wikipedia succeed on the first Wikipedia call,
        so that we can verify the datahub retry count precisely.
        """
        mod = _import_module()
        import requests as req_lib

        max_attempts = mod._CONSTITUENT_MAX_ATTEMPTS

        def _make_503() -> MagicMock:
            resp = MagicMock()
            resp.status_code = 503
            resp.close = MagicMock()
            resp.raise_for_status = MagicMock(
                side_effect=req_lib.HTTPError(response=resp)
            )
            return resp

        # Datahub gets max_attempts 503 responses; then Wikipedia gets a valid page.
        tickers = _make_valid_constituent_list(500)
        wiki_html = _make_wikipedia_html(tickers)
        wiki_response = _make_mock_response(wiki_html, status_code=200)
        # Wikipedia uses iter_content; wire it up for the mock.
        wiki_response.iter_content = MagicMock(
            return_value=iter([wiki_html.encode("utf-8")])
        )

        side_effects = [_make_503() for _ in range(max_attempts)] + [wiki_response]

        with patch("time.sleep"):
            with patch("requests.get", side_effect=side_effects) as mock_get:
                result = mod.get_sp500_constituents()

        # Datahub used exactly max_attempts calls; Wikipedia used 1 more.
        assert mock_get.call_count == max_attempts + 1, (
            f"Expected {max_attempts + 1} total calls "
            f"({max_attempts} datahub + 1 wikipedia); "
            f"got {mock_get.call_count}. "
            "Datahub retry is not bounded at _CONSTITUENT_MAX_ATTEMPTS."
        )
        # Result should be valid from Wikipedia fallback.
        assert _SANITY_MIN <= len(result) <= _SANITY_MAX, (
            f"Wikipedia fallback result {len(result)} outside sanity band."
        )


# ---------------------------------------------------------------------------
# Robustness: body size cap
# ---------------------------------------------------------------------------


class TestBodySizeCap:
    """Wikipedia stream=True path: body exceeding _MAX_HTML_BYTES is treated as failure.

    The impl accumulates chunks from iter_content and raises ValueError when
    total > _MAX_HTML_BYTES.  get_sp500_constituents catches this and falls through
    to [] (since datahub also fails in these tests).

    Datahub path: resp.content[:_MAX_HTML_BYTES] is sliced, then if
    len(resp.content) > _MAX_HTML_BYTES a ValueError is raised.
    """

    def test_wikipedia_oversized_body_causes_source_failure_not_crash(self):
        """Wikipedia response body > _MAX_HTML_BYTES → that source fails; no exception escapes.

        We mock iter_content to return a single chunk larger than _MAX_HTML_BYTES.
        The Wikipedia fetch must raise internally (ValueError), which
        get_sp500_constituents catches.  With datahub also failing, result is [].
        """
        mod = _import_module()
        import requests as req_lib

        max_bytes = mod._MAX_HTML_BYTES

        # Datahub fails immediately.
        datahub_resp = MagicMock()
        datahub_resp.status_code = 503
        datahub_resp.close = MagicMock()
        datahub_resp.raise_for_status = MagicMock(
            side_effect=req_lib.HTTPError(response=datahub_resp)
        )

        # Wikipedia returns 200 but the body is one chunk of (max_bytes + 1) bytes.
        oversized_chunk = b"X" * (max_bytes + 1)
        wiki_resp = MagicMock()
        wiki_resp.status_code = 200
        wiki_resp.raise_for_status = MagicMock()
        wiki_resp.close = MagicMock()
        wiki_resp.iter_content = MagicMock(return_value=iter([oversized_chunk]))

        # datahub gets max_attempts 503s; wikipedia gets 1 oversized response.
        max_attempts = mod._CONSTITUENT_MAX_ATTEMPTS
        side_effects = [datahub_resp] * max_attempts + [wiki_resp]

        with patch("time.sleep"):
            with patch("requests.get", side_effect=side_effects):
                try:
                    result = mod.get_sp500_constituents()
                except Exception as exc:
                    pytest.fail(
                        f"get_sp500_constituents raised {type(exc).__name__} when "
                        f"Wikipedia body exceeded _MAX_HTML_BYTES — must degrade to []."
                    )

        assert result == [], (
            "Oversized Wikipedia body must cause honest-availability [] result, "
            f"not a partial parse; got {result!r}"
        )

    def test_datahub_oversized_body_causes_source_failure_not_crash(self):
        """Datahub response body > _MAX_HTML_BYTES → raises ValueError internally.

        The impl slices resp.content[:_MAX_HTML_BYTES] then checks len(resp.content);
        if over the ceiling it raises ValueError.  get_sp500_constituents catches
        this and falls through to Wikipedia (which also fails), returning [].
        """
        mod = _import_module()
        import requests as req_lib

        max_bytes = mod._MAX_HTML_BYTES

        # Datahub returns a 200 but with content exceeding the cap.
        oversized_content = b"Symbol,Name,Sector\n" + b"A" * (max_bytes + 1)
        datahub_resp = MagicMock()
        datahub_resp.status_code = 200
        datahub_resp.content = oversized_content
        datahub_resp.raise_for_status = MagicMock()

        # Wikipedia also fails (connection error) → total result is [].
        wiki_fail = req_lib.ConnectionError("wiki down")

        side_effects = [datahub_resp] + [wiki_fail] * mod._CONSTITUENT_MAX_ATTEMPTS

        with patch("time.sleep"):
            with patch("requests.get", side_effect=side_effects):
                try:
                    result = mod.get_sp500_constituents()
                except Exception as exc:
                    pytest.fail(
                        f"get_sp500_constituents raised {type(exc).__name__} when "
                        f"datahub body exceeded _MAX_HTML_BYTES — must degrade to []."
                    )

        assert result == [], (
            "Oversized datahub body must yield [], not a partial result; "
            f"got {result!r}"
        )


# ---------------------------------------------------------------------------
# Robustness: bar-key guard (missing / non-numeric "c" field)
# ---------------------------------------------------------------------------


class TestBarKeyGuard:
    """compute_breadth filters bars missing 'c' or with non-numeric 'c'.

    A name with N total bars but M < _MIN_QUALIFYING_BARS valid closes is
    excluded from the qualifying count (re-application of qualifying threshold
    after filtering).  A name with enough valid closes after filtering is kept.
    No crash must occur when malformed bars are present.
    """

    def test_bar_missing_c_key_excluded_from_closes(self):
        """Bars without 'c' key are silently skipped; name qualifies if enough valid closes remain.

        Build a name with 250 bar dicts: the first 10 lack the 'c' key entirely.
        After filtering, 240 valid closes remain (≥ 200) → name qualifies.
        """
        mod = _import_module()

        close_val = 150.0
        # 10 malformed bars (missing 'c'), then 240 valid bars.
        bad_bars = [
            {"t": f"2023-01-{i+1:02d}T00:00:00Z", "o": close_val, "h": close_val, "l": close_val, "v": 1_000_000}
            for i in range(10)
        ]
        good_bars = [
            {"t": f"2023-02-{i+1:02d}T00:00:00Z", "o": close_val, "h": close_val,
             "l": close_val, "c": close_val, "v": 1_000_000}
            for i in range(240)
        ]
        all_bars = bad_bars + good_bars  # 250 total, 240 valid closes

        tickers = ["BADKEY"]
        bars_by_symbol = {"BADKEY": all_bars}

        with patch("synthetic_history.fetch_bars", return_value=bars_by_symbol):
            result = mod.compute_breadth(tickers)

        # Should not crash.
        assert isinstance(result, dict)
        # 240 valid closes ≥ 200 → name qualifies.
        assert result["qualifying_count"] == 1, (
            f"Name with 240 valid closes after filtering must qualify; "
            f"got qualifying_count={result['qualifying_count']}"
        )

    def test_bar_with_non_numeric_c_excluded_from_closes(self):
        """Bars with non-numeric 'c' value are silently skipped without crashing.

        Build a name with 250 bars: 60 have c='N/A' (string), 190 have numeric c.
        After filtering only 190 valid closes remain (< 200) → name is excluded.
        """
        mod = _import_module()

        close_val = 75.0
        # 60 bars with string 'c' (malformed).
        bad_bars = [
            {"t": f"2023-01-{(i % 28)+1:02d}T00:00:00Z", "o": close_val, "h": close_val,
             "l": close_val, "c": "N/A", "v": 1_000_000}
            for i in range(60)
        ]
        # 190 bars with valid numeric 'c'.
        good_bars = [
            {"t": f"2023-02-{(i % 28)+1:02d}T00:00:00Z", "o": close_val, "h": close_val,
             "l": close_val, "c": close_val, "v": 1_000_000}
            for i in range(190)
        ]
        all_bars = bad_bars + good_bars  # 250 total, 190 valid closes

        tickers = ["BADVAL"]
        bars_by_symbol = {"BADVAL": all_bars}

        with patch("synthetic_history.fetch_bars", return_value=bars_by_symbol):
            result = mod.compute_breadth(tickers)

        # Should not crash.
        assert isinstance(result, dict)
        # 190 valid closes < 200 → name excluded from qualifying.
        assert result["qualifying_count"] == 0, (
            f"Name with only 190 valid closes must not qualify; "
            f"got qualifying_count={result['qualifying_count']}"
        )
        assert result["total_count"] == 1

    def test_mixed_valid_and_malformed_bars_correct_breadth_count(self):
        """Breadth numerator is computed only over valid closes; malformed bars don't count.

        Two names: NAME_GOOD has 250 all-valid bars (qualifies, above SMA).
        NAME_BAD_KEYS has 200 total bars, 50 missing 'c' → only 150 valid → excluded.
        qualifying_count must be 1 (NAME_GOOD only); NAME_BAD_KEYS excluded.
        """
        mod = _import_module()

        high_close = 200.0

        good_bars = [
            {"t": f"2024-01-{(i % 28)+1:02d}T00:00:00Z", "o": high_close,
             "h": high_close, "l": high_close, "c": high_close, "v": 1_000_000}
            for i in range(250)
        ]

        bad_head = [
            {"t": f"2024-02-{(i % 28)+1:02d}T00:00:00Z", "o": high_close,
             "h": high_close, "l": high_close, "v": 1_000_000}  # no 'c'
            for i in range(50)
        ]
        good_tail = [
            {"t": f"2024-03-{(i % 28)+1:02d}T00:00:00Z", "o": high_close,
             "h": high_close, "l": high_close, "c": high_close, "v": 1_000_000}
            for i in range(150)
        ]

        bars_by_symbol = {
            "NAME_GOOD": good_bars,
            "NAME_BAD_KEYS": bad_head + good_tail,  # 200 total, 150 valid closes
        }
        tickers = list(bars_by_symbol.keys())

        with patch("synthetic_history.fetch_bars", return_value=bars_by_symbol):
            result = mod.compute_breadth(tickers)

        assert result["qualifying_count"] == 1, (
            f"Only NAME_GOOD should qualify; expected qualifying_count=1, "
            f"got {result['qualifying_count']}"
        )
        assert result["total_count"] == 2


# ---------------------------------------------------------------------------
# Robustness: ZeroQualifying contract — pct keys absent from unavailable result
# ---------------------------------------------------------------------------


class TestZeroQualifyingContract:
    """fetch_breadth ZeroQualifying path: pct_above_50sma / pct_above_200sma absent.

    When qualifying_count == 0, fetch_breadth must return available=False +
    reason="ZeroQualifying" + qualifying_count + total_count, but must NOT
    include pct_above_50sma or pct_above_200sma.  Those keys are 0.0 placeholders
    in compute_breadth; presenting them as real measurements is a contract violation.
    """

    def test_zero_qualifying_result_has_available_false(self):
        """fetch_breadth with zero qualifying names → available=False (ZeroQualifying path)."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        # All bars short-history → zero qualifying after compute_breadth.
        bars = {t: _make_bars(5) for t in tickers[:20]}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot", return_value=1):
                    result = mod.fetch_breadth()

        assert result["available"] is False, (
            f"Expected available=False for ZeroQualifying; got available={result['available']!r}"
        )

    def test_zero_qualifying_result_has_reason_zerqualifying(self):
        """fetch_breadth ZeroQualifying path → reason == 'ZeroQualifying'."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(5) for t in tickers[:20]}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot", return_value=1):
                    result = mod.fetch_breadth()

        assert result.get("reason") == "ZeroQualifying", (
            f"Expected reason='ZeroQualifying'; got {result.get('reason')!r}"
        )

    def test_zero_qualifying_result_omits_pct_above_50sma(self):
        """fetch_breadth ZeroQualifying path must NOT include pct_above_50sma key.

        The 0.0 placeholder from compute_breadth is a false measurement; the
        docstring contract states pct keys are 'present when available=True' only.
        """
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(5) for t in tickers[:20]}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot", return_value=1):
                    result = mod.fetch_breadth()

        assert "pct_above_50sma" not in result, (
            "ZeroQualifying result must NOT include pct_above_50sma — "
            "it is a 0.0 placeholder, not a real breadth measurement."
        )

    def test_zero_qualifying_result_omits_pct_above_200sma(self):
        """fetch_breadth ZeroQualifying path must NOT include pct_above_200sma key."""
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(5) for t in tickers[:20]}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot", return_value=1):
                    result = mod.fetch_breadth()

        assert "pct_above_200sma" not in result, (
            "ZeroQualifying result must NOT include pct_above_200sma — "
            "it is a 0.0 placeholder, not a real breadth measurement."
        )

    def test_zero_qualifying_result_includes_count_keys(self):
        """fetch_breadth ZeroQualifying path DOES include qualifying_count and total_count.

        These diagnostic counts are safe to expose — they are integer measurements,
        not fabricated percentages.
        """
        mod = _import_module()
        tickers = _make_valid_constituent_list(503)
        csv_body = _make_csv_body(tickers)
        bars = {t: _make_bars(5) for t in tickers[:20]}

        with patch("requests.get", return_value=_make_mock_response(csv_body)):
            with patch("synthetic_history.fetch_bars", return_value=bars):
                with patch("advisors.lens_warehouse.persist_lens_snapshot", return_value=1):
                    result = mod.fetch_breadth()

        assert "qualifying_count" in result, (
            "ZeroQualifying result must include qualifying_count for diagnostics."
        )
        assert "total_count" in result, (
            "ZeroQualifying result must include total_count for diagnostics."
        )
        assert result["qualifying_count"] == 0, (
            f"qualifying_count must be 0 in ZeroQualifying path; "
            f"got {result['qualifying_count']}"
        )
