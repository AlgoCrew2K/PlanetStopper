"""RED tests — Phase 3.6 Strategy Builder: Inline-SVG Sparklines.

Phase 3.6 adds equity-curve sparklines to the Strategy Builder dashboard.
Three production-file changes are under test:

1. ``advisors/strategy_builder_engine.py``: two new pure-CPU helpers
   ``_cumulative_returns(returns_pct)`` and ``_downsample(series, target=60)``.
   In ``_persist_survivor``, before the ``_sanitize_non_finite(raw_response)``
   call, if ``_returns_pct`` is non-empty, the helper adds::

       raw_response["equity_curve_downsampled"] = _downsample(
           _cumulative_returns(_returns_pct)
       )

   ``_sanitize_non_finite`` then processes the whole dict (including the new
   list) so the persisted series is always RFC-7159-clean.

2. ``app.py`` GET ``/ai-advisor/strategy-builder``: in the per-obs loop (around
   line 3309), after building ``card_artifacts[obs_id]``::

       obs['sparkline_points'] = rr.get('equity_curve_downsampled')

   NOT added to ``card_artifacts`` — ``CHAT_ARTIFACT_ALLOWED_FIELDS`` is FROZEN.

3. ``templates/ai_advisor_strategy_builder.html``: adds a
   ``{% macro render_sparkline(points) %}`` macro (~20 lines), called on
   survivor cards AND withheld/rejected cards. The macro renders an inline
   ``<svg>`` polyline only when ``{% if points %}`` is True — empty list and
   None are both falsy so neither renders any SVG stub.

Hard requirements under test:
  HR-1: Old rows (no ``equity_curve_downsampled`` in raw_response) render
        exactly as before — no empty ``<svg>`` stubs, no crashes.
  HR-2: Persisted series ≤ 2 KB. Test: len(json.dumps(series)) ≤ 2048 for a
        1250-point input downsampled via ``_downsample(_cumulative_returns(...))``.
  HR-3: Downsampling determinism — same input → same output; first AND last
        points preserved exactly; series ≤ 60 points returned unchanged.
  HR-4: SVG macro must guard (skip render) if any point is not a number (e.g.
        None from sanitisation). Defence-in-depth vs. ``_sanitize_non_finite``.
  HR-5: All prior invariants pass (baseline 6025/4/0).

Mocking strategy:
  - DS/SB tests: pure import + call; no mocking (helpers are pure CPU).
  - PP tests: patch ``database.insert_advisor_observation``; capture call_args.
  - BC/SR tests: Flask test client; inject fixture obs via
    ``patch("database.get_advisor_observations_for_symphony")``.
  - All fixtures are function-scoped; no module-level mutables.
  - No live API calls; no ``@pytest.mark.live`` here.

Fixture path used by BC tests:
  tests/fixtures/ai_advisor/m6/strategy_builder_observations_basic.json
  (pre-3.5/3.6 fixture — no ``equity_curve_downsampled`` in raw_response)

==========================================================================
CYCLE 1 — RED tests (must FAIL before implementation)
==========================================================================
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Repository root and fixture paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_BASIC_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "ai_advisor"
    / "m6"
    / "strategy_builder_observations_basic.json"
)


def _load_basic_fixture() -> dict:
    """Load the pre-3.6 observations fixture (no equity_curve_downsampled in raw_response)."""
    return json.loads(_BASIC_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Flask test client fixture (function-scoped — no shared state)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Flask test client with testing mode enabled."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers: build CandidateInfo + CandidateGateResult stubs for PP tests
# (mirrors the pattern from test_strategy_builder_phase35.py)
# ---------------------------------------------------------------------------


def _make_candidate_info(metrics: dict | None = None):
    """Return a minimal CandidateInfo with the given metrics dict."""
    from advisors.strategy_builder_engine import CandidateInfo

    return CandidateInfo(
        candidate_id="test:T1:equal_weight",
        tree={},
        template_id="T1",
        params={"objective": "diversify", "tickers": ["SPY", "AGG"]},
        metrics=metrics
        if metrics is not None
        else {
            # Sentinel values — signs/types match the contract; not producer floats.
            "annualized_return": 0.08,
            "sharpe": 0.6,
            "calmar": 0.5,
            "max_drawdown": -0.15,
            "sortino": 0.7,
            "total_return": 0.40,
            "win_rate": 0.54,
            "volatility": 0.12,
        },
    )


def _make_gate_result(
    candidate_id: str = "test:T1:equal_weight", verdict_str: str = "ADOPT_CANDIDATE"
):
    """Return a minimal CandidateGateResult stub (MagicMock — decoupled from field-count).

    Uses MagicMock so that changes to the NamedTuple's field list in
    backtest_gate_engine.py do not break the test helper. Only the attributes
    that ``_persist_survivor`` reads are explicitly set.
    """
    verdict_mock = MagicMock()
    verdict_mock.decision = verdict_str

    result = MagicMock()
    result.candidate_id = candidate_id
    result.verdict = verdict_mock
    result.winner_p_adj = 0.012
    result.caveats = ["Selected on backtest"]
    return result


def _make_phase36_obs(obs_id: int = 601, sparkline: list | None = None) -> dict:
    """Build a Phase 3.6 observation dict with equity_curve_downsampled in raw_response.

    ``sparkline=None`` uses a small default list.  Pass an explicit list to
    control the series (use ``[]`` to test the empty-list guard, or include
    ``None`` values to test HR-4).
    """
    _sparkline = (
        [0.0, 0.5, 1.2, 0.8, 1.5, 1.8, 1.3, 2.0, 1.7, 2.2,
         2.5, 2.1, 2.8, 3.0, 2.7, 3.2, 3.5, 3.1, 3.7, 4.0]
        if sparkline is None
        else sparkline
    )
    return {
        "id": obs_id,
        "created_at": "2026-06-12T10:00:00",
        "advisor_role": "STRATEGY_BUILDER",
        "subject_type": "strategy_proposal",
        "subject_id": f"sym-36:{obs_id}",
        "verdict": "ADOPT_CANDIDATE",
        "raw_response": {
            "objective": "diversify",
            "template_id": "T1",
            "candidate_id": f"sym-36:{obs_id}:T1",
            "metrics": {},
            "equity_curve_downsampled": _sparkline,
            "cagr": 0.09,
            "sharpe": 0.65,
            "calmar": 0.48,
            "max_drawdown": -0.18,
            "n_candidates": 4,
            "n_survivors": 1,
            "gate_decision": "ADOPT_CANDIDATE",
            "winner_p_adj": 0.010,
            "fdr_q": 0.05,
            "fdr_adjusted_threshold": 0.017,
            "caveats": [],
            "rules_text": "Equal weight: SPY, AGG",
        },
        "is_advisory_only": 1,
        "spec_bundle_id": None,
    }


# ===========================================================================
# Group DS: Downsampling helpers (unit tests — pure CPU, no mocking needed)
# These will FAIL with ImportError before implementation — that is the RED state.
# ===========================================================================


class TestGroupDS:
    """DS: _downsample and _cumulative_returns unit tests (HR-3)."""

    def test_downsample_determinism(self):
        """DS-1: _downsample returns identical results on two calls with the same input.

        HR-3: downsampling must be deterministic — same input always produces
        the same output. Non-determinism would cause SVG coordinates to change
        on every page load, undermining the sparkline's readability.
        """
        from advisors.strategy_builder_engine import _downsample

        series = [float(i) / 10 for i in range(200)]
        result_a = _downsample(series)
        result_b = _downsample(series)

        assert result_a == result_b, (
            f"_downsample produced different results on two calls with the same input "
            f"(len={len(series)}). HR-3: downsampling must be deterministic. "
            f"First call length={len(result_a)}, second call length={len(result_b)}."
        )

    def test_downsample_first_last_preserved(self):
        """DS-2: _downsample preserves the first and last points of the input series.

        HR-3: the first and last points must be preserved exactly so that the
        sparkline always shows the entry NAV and the final NAV of the candidate.
        Losing either endpoint distorts the visual return story.
        """
        from advisors.strategy_builder_engine import _downsample

        series = [float(i) / 10 for i in range(200)]
        result = _downsample(series)

        assert result[0] == series[0], (
            # Tolerance: exact equality — the first point is copied, not interpolated.
            f"_downsample did not preserve the first point. "
            f"Expected result[0]={series[0]!r}, got result[0]={result[0]!r}. "
            "HR-3: first point must be preserved exactly to anchor the sparkline baseline."
        )
        assert result[-1] == series[-1], (
            # Tolerance: exact equality — the last point is copied, not interpolated.
            f"_downsample did not preserve the last point. "
            f"Expected result[-1]={series[-1]!r}, got result[-1]={result[-1]!r}. "
            "HR-3: last point must be preserved exactly to show the final return."
        )

    def test_downsample_short_series_unchanged(self):
        """DS-3: _downsample returns the input unchanged when len(series) < target (60).

        HR-3: series shorter than the target need no downsampling — returning them
        unchanged avoids any interpolation loss and keeps the test for DS-2 trivially
        satisfied.
        """
        from advisors.strategy_builder_engine import _downsample

        series = [float(i) * 0.5 for i in range(30)]  # 30 < 60
        result = _downsample(series)

        assert result == series, (
            f"_downsample modified a series shorter than target=60. "
            f"Input length={len(series)}, output length={len(result)}. "
            "HR-3: series with len < target must be returned unchanged."
        )

    def test_downsample_exactly_60_points_unchanged(self):
        """DS-4: _downsample returns the input unchanged when len(series) == target (60).

        HR-3: a 60-point series is already at the target — no downsampling needed.
        Modifying it would lose information unnecessarily and violate the 'unchanged'
        contract for series at or below the target.
        """
        from advisors.strategy_builder_engine import _downsample

        series = [float(i) * 0.1 for i in range(60)]  # exactly 60
        result = _downsample(series)

        assert result == series, (
            f"_downsample modified a 60-point series (equal to target=60). "
            f"Input length={len(series)}, output length={len(result)}. "
            "HR-3: series with len == target must be returned unchanged."
        )

    def test_downsample_large_series_at_most_60_points(self):
        """DS-5: _downsample returns at most 60 points for a 1250-point input.

        HR-2/HR-3: the size budget test (SB-1) depends on this bound. A >60-point
        output would produce a JSON blob larger than the 2 KB budget.
        """
        from advisors.strategy_builder_engine import _downsample

        series = [float(i) * 0.01 for i in range(1250)]
        result = _downsample(series)

        assert len(result) <= 60, (
            f"_downsample returned {len(result)} points for a 1250-point input. "
            "HR-3: output must be ≤ 60 points (the target). "
            "Exceeding 60 points violates the HR-2 2 KB size budget."
        )

    def test_cumulative_returns_running_sum(self):
        """DS-6: _cumulative_returns([1.0, 2.0, 3.0]) returns the running sum [1.0, 3.0, 6.0].

        The cumulative return series is used as the y-axis for the sparkline.
        Each element i is the sum of returns_pct[0..i] so the curve represents
        total-return-from-start at each time step.
        """
        from advisors.strategy_builder_engine import _cumulative_returns

        result = _cumulative_returns([1.0, 2.0, 3.0])

        assert result == pytest.approx([1.0, 3.0, 6.0], abs=1e-6), (
            # Tolerance: 1e-6 — cumulative summation of small integers; floating-point
            # error is negligible; the larger tolerance (1e-6) guards only against
            # implementation rounding choices.
            f"_cumulative_returns([1.0, 2.0, 3.0]) returned {result!r}. "
            "Expected [1.0, 3.0, 6.0] (running sum). "
            "DS-6: each element must equal sum(returns_pct[0..i])."
        )

    def test_cumulative_returns_empty(self):
        """DS-7: _cumulative_returns([]) returns [].

        An empty candidate returns series must produce an empty cumulative series
        so the downstream '' guard in _persist_survivor and the Jinja macro
        both short-circuit correctly without IndexError.
        """
        from advisors.strategy_builder_engine import _cumulative_returns

        result = _cumulative_returns([])

        assert result == [], (
            f"_cumulative_returns([]) returned {result!r}. "
            "Expected [] (empty list). DS-7: empty input must yield empty output."
        )

    def test_cumulative_returns_single_point(self):
        """DS-8: _cumulative_returns([5.0]) returns [5.0].

        A single-point series is a degenerate but valid input. The cumulative
        sum of one element is that element itself.
        """
        from advisors.strategy_builder_engine import _cumulative_returns

        result = _cumulative_returns([5.0])

        assert result == pytest.approx([5.0], abs=1e-9), (
            # Tolerance: 1e-9 — single float; no summation error possible.
            f"_cumulative_returns([5.0]) returned {result!r}. "
            "Expected [5.0]. DS-8: single-element input must return that element."
        )

    def test_cumulative_returns_decimal_rounding(self):
        """DS-9: _cumulative_returns rounds each cumulative value to 2 decimal places.

        The contract rounds to 2 dp to keep the JSON payload small (fewer bytes
        per coordinate) and to avoid long floating-point tails in SVG path strings.

        Verification series: [0.15, 0.15]
          - cumulative[0] = 0.15 (round(0.15, 2) = 0.15)
          - cumulative[1] = 0.30 (round(0.15 + 0.15, 2) = 0.30)
        This is chosen because 0.15 + 0.15 = 0.30 exactly in IEEE-754, so rounding
        does not mask the assertion.  Use abs=1e-6 to guard against implementation
        choices (e.g. round vs truncate).
        """
        from advisors.strategy_builder_engine import _cumulative_returns

        result = _cumulative_returns([0.15, 0.15])

        assert len(result) == 2, (
            f"_cumulative_returns([0.15, 0.15]) returned {len(result)} elements. "
            "Expected 2 elements."
        )
        assert result[0] == pytest.approx(0.15, abs=1e-6), (
            # Tolerance: 1e-6 — rounding to 2 dp; first element equals input.
            f"_cumulative_returns([0.15, 0.15])[0]={result[0]!r}. "
            "Expected 0.15 (round(0.15, 2)). DS-9: rounding must be to 2 decimal places."
        )
        assert result[1] == pytest.approx(0.30, abs=1e-6), (
            # Tolerance: 1e-6 — 0.15+0.15 = 0.30 exactly; rounding to 2 dp preserves.
            f"_cumulative_returns([0.15, 0.15])[1]={result[1]!r}. "
            "Expected 0.30 (round(0.15 + 0.15, 2)). DS-9: cumulative sum rounded to 2 dp."
        )


# ===========================================================================
# Group SB: Size budget (HR-2)
# ===========================================================================


class TestGroupSB:
    """SB: Serialized equity_curve_downsampled must fit within 2 KB (HR-2)."""

    def test_size_budget_1250_point_input(self):
        """SB-1: _downsample(_cumulative_returns(1250-point input)) serializes to ≤ 2048 bytes.

        HR-2: the persisted equity_curve_downsampled field must be ≤ 2 KB so that
        the full raw_response JSON stays within a reasonable SQLite cell size.
        A 1250-point input represents a full 5-year trading-day series.
        The downsampling must reduce it to ≤ 60 points whose JSON is ≤ 2048 bytes.
        """
        from advisors.strategy_builder_engine import _cumulative_returns, _downsample

        # 1250 float values in [0, 125.0] — realistic cumulative return magnitudes.
        raw_series = [float(i) * 0.1 for i in range(1250)]
        cumulative = _cumulative_returns(raw_series)
        downsampled = _downsample(cumulative)

        serialized = json.dumps(downsampled)
        byte_count = len(serialized)

        assert byte_count <= 2048, (
            f"json.dumps(_downsample(_cumulative_returns(1250-point series))) "
            f"= {byte_count} bytes, exceeds 2048-byte budget. "
            "HR-2: the persisted equity_curve_downsampled series must be ≤ 2 KB. "
            f"Downsampled to {len(downsampled)} points. "
            "Reduce the number of kept points or truncate float precision."
        )


# ===========================================================================
# Group PP: Persist payload shape
# ===========================================================================


class TestGroupPP:
    """PP: _persist_survivor includes equity_curve_downsampled when returns_pct provided."""

    def test_persist_survivor_payload_has_equity_curve_downsampled_when_returns_pct_provided(
        self,
    ):
        """PP-1: _persist_survivor raw_response contains 'equity_curve_downsampled' when
        a non-empty _returns_pct is present on the CandidateInfo object.

        The implementer attaches _returns_pct to info before calling _persist_survivor.
        We capture the raw_response passed to database.insert_advisor_observation and
        verify the key is present. We do NOT assert the values — they depend on the
        implementation of _cumulative_returns + _downsample (HR-2: no hardcoded values).
        """
        from advisors.strategy_builder_engine import _persist_survivor

        info = _make_candidate_info()
        # Inject a non-empty returns_pct series via the test seam.
        info._returns_pct = [float(i) * 0.01 for i in range(100)]
        gate_result = _make_gate_result()

        with patch("database.insert_advisor_observation") as mock_insert:
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=4,
                live_returns=[0.1, -0.05, 0.08],
                n_survivors=1,
            )

        assert mock_insert.called, (
            "_persist_survivor must call database.insert_advisor_observation."
        )
        rr = mock_insert.call_args.kwargs.get("raw_response", {})

        assert "equity_curve_downsampled" in rr, (
            "'equity_curve_downsampled' key is absent from the persisted raw_response. "
            "PP-1: when _returns_pct is non-empty, _persist_survivor must add "
            "'equity_curve_downsampled' = _downsample(_cumulative_returns(_returns_pct)) "
            "to raw_response before the _sanitize_non_finite call (Phase 3.6 contract)."
        )

    def test_persist_survivor_payload_no_equity_curve_when_no_returns_pct(self):
        """PP-2: 'equity_curve_downsampled' is ABSENT from raw_response when _returns_pct is empty.

        [PM-ASSUMED]: if _returns_pct is empty or absent, no equity curve is persisted.
        The key must not exist (not be None, not be []) — its absence is the contract.
        Old-row backward compat (HR-1) depends on this: rows without returns data must
        not get an empty list injected that the template would see as truthy.
        """
        from advisors.strategy_builder_engine import _persist_survivor

        info = _make_candidate_info()
        # Explicitly empty returns_pct — no equity curve expected.
        info._returns_pct = []
        gate_result = _make_gate_result()

        with patch("database.insert_advisor_observation") as mock_insert:
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=4,
                live_returns=[],
                n_survivors=1,
            )

        rr = mock_insert.call_args.kwargs.get("raw_response", {})

        assert "equity_curve_downsampled" not in rr, (
            f"'equity_curve_downsampled' key is present in raw_response when "
            "_returns_pct=[] (empty). "
            "PP-2: the key must be ABSENT (not None, not []) when no returns are available. "
            f"Got raw_response keys: {list(rr.keys())!r}. "
            "[PM-ASSUMED]: empty returns_pct → no equity curve field persisted."
        )

    def test_persist_survivor_equity_curve_passes_through_sanitize_non_finite(self):
        """PP-3: persisted equity_curve_downsampled contains only finite floats or None — no NaN/inf.

        The contract places the equity_curve computation BEFORE _sanitize_non_finite so
        that any non-finite values produced by _cumulative_returns are cleaned up.
        _cumulative_returns of finite inputs should always be finite, but the
        sanitization boundary must be respected for defence-in-depth.

        Assert: every element is either None or a finite float (no NaN, no ±inf).
        """
        import math

        from advisors.strategy_builder_engine import _persist_survivor

        info = _make_candidate_info()
        info._returns_pct = [0.01 * i for i in range(50)]  # normal floats
        gate_result = _make_gate_result()

        with patch("database.insert_advisor_observation") as mock_insert:
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=4,
                live_returns=[0.1, -0.05, 0.08],
                n_survivors=1,
            )

        rr = mock_insert.call_args.kwargs.get("raw_response", {})
        curve = rr.get("equity_curve_downsampled", [])

        assert isinstance(curve, list), (
            f"equity_curve_downsampled must be a list, got {type(curve).__name__!r}. "
            "PP-3: the field must be a JSON array."
        )

        for i, v in enumerate(curve):
            assert v is None or (isinstance(v, float) and math.isfinite(v)), (
                f"equity_curve_downsampled[{i}]={v!r} is neither None nor a finite float. "
                "PP-3: _sanitize_non_finite must convert any NaN/inf to None before persist. "
                "The equity curve must contain only finite floats or None."
            )

    def test_persist_rejected_also_has_equity_curve_downsampled(self):
        """PP-4: Rejected-candidate persist also adds 'equity_curve_downsampled' when
        _returns_pct is non-empty.

        [PM-ASSUMED from contract §2]: rejected candidates also get sparklines.
        The sparkline is a property of the candidate's backtest — not a survivor property.
        Assert: 'equity_curve_downsampled' key is present in the raw_response for a
        rejected (is_rejected=True) persist call with a non-empty _returns_pct.
        """
        from advisors.strategy_builder_engine import _persist_survivor

        info = _make_candidate_info()
        info._returns_pct = [float(i) * 0.01 for i in range(80)]
        gate_result = _make_gate_result(verdict_str="WITHHELD_FDR")

        with patch("database.insert_advisor_observation") as mock_insert:
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=4,
                live_returns=[],
                n_survivors=0,
                is_rejected=True,
            )

        rr = mock_insert.call_args.kwargs.get("raw_response", {})

        assert "equity_curve_downsampled" in rr, (
            "'equity_curve_downsampled' key is absent from a rejected-candidate raw_response "
            "when _returns_pct is non-empty. "
            "PP-4: rejected candidates must also persist the equity curve sparkline data. "
            "[PM-ASSUMED]: sparklines are candidate-scoped, not survivor-scoped."
        )


# ===========================================================================
# Group BC: Backward compat / old-row rendering (HR-1)
# These use the Flask test client and the pre-3.6 fixture.
# ===========================================================================


class TestGroupBC:
    """BC: Pre-3.6 obs rows (no equity_curve_downsampled) render exactly as before (HR-1)."""

    def test_old_row_renders_no_svg_sparkline(self, client):
        """BC-1: A pre-3.6 observation (no equity_curve_downsampled) renders without any <svg>.

        HR-1: old rows must render exactly as today — no empty <svg> stubs, no crashes.
        The macro must be a no-op when sparkline_points is None (the route sets it to
        rr.get('equity_curve_downsampled') which is None for old rows).
        """
        fixture = _load_basic_fixture()
        obs = fixture["observation_survivor"]

        # Confirm this is genuinely a pre-3.6 row.
        assert "equity_curve_downsampled" not in obs["raw_response"], (
            "Test setup error: the pre-3.6 fixture already has 'equity_curve_downsampled'. "
            "This test requires a row without that field."
        )

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

        assert resp.status_code == 200, (
            f"GET /ai-advisor/strategy-builder returned {resp.status_code} for a pre-3.6 "
            "observation row. HR-1: old rows must render without crashing."
        )

        html = resp.get_data(as_text=True)

        assert "<svg" not in html, (
            "Rendered HTML contains '<svg' for a pre-3.6 observation (no equity_curve_downsampled). "
            "HR-1: old rows must not produce any SVG element — no empty stubs. "
            "The macro must guard with '{% if points %}' and old rows have points=None."
        )

    def test_old_row_renders_no_crash(self, client):
        """BC-2: A pre-3.6 observation renders without a Python traceback (HTTP 200, no Traceback).

        HR-1 explicit crash check: separate from BC-1 to make the constraint explicit.
        Even if an SVG stub appeared, this test would still fail independently if a
        server-side exception surfaced in the response.
        """
        fixture = _load_basic_fixture()
        obs = fixture["observation_survivor"]

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

        assert resp.status_code == 200, (
            f"GET returned {resp.status_code} for pre-3.6 fixture observation. "
            "BC-2 / HR-1: old rows must not crash the route."
        )

        html = resp.get_data(as_text=True)
        assert "Traceback" not in html, (
            "Rendered HTML contains 'Traceback' for a pre-3.6 observation. "
            "BC-2 / HR-1: a Python exception must not be surfaced to the page."
        )

    def test_old_row_sparkline_points_is_none_in_context(self, client):
        """BC-3: After GET with an old-row obs, obs['sparkline_points'] is None in the template.

        The route does: obs['sparkline_points'] = rr.get('equity_curve_downsampled')
        For a pre-3.6 row, 'equity_curve_downsampled' is absent → rr.get(...) returns
        None. The template then receives sparkline_points=None and the macro skips render.

        We capture the kwargs passed to render_template and check the observations list.
        """
        fixture = _load_basic_fixture()
        obs = fixture["observation_survivor"]

        captured: dict = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return "<html><body>stub</body></html>"

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
            patch.object(app_module, "render_template", side_effect=_capture),
        ):
            client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

        observations_ctx = captured.get("observations", [])
        assert observations_ctx, (
            "render_template was not called with an 'observations' kwarg. "
            "BC-3: the route must pass observations to the template."
        )

        for ctx_obs in observations_ctx:
            sparkline = ctx_obs.get("sparkline_points")
            assert sparkline is None, (
                f"obs['sparkline_points']={sparkline!r} for a pre-3.6 row. "
                "Expected None — rr.get('equity_curve_downsampled') must return None "
                "when the key is absent. "
                "BC-3: the route must not inject a default or empty list for old rows."
            )


# ===========================================================================
# Group SR: SVG render correctness (new rows WITH equity_curve_downsampled)
# ===========================================================================


class TestGroupSR:
    """SR: New-row obs with equity_curve_downsampled renders correct SVG sparkline."""

    def test_new_row_renders_svg_sparkline(self, client):
        """SR-1: A new obs with equity_curve_downsampled renders at least one <svg> element.

        The macro ``render_sparkline(points)`` must emit an inline <svg> when points
        is a non-empty list of numbers. The SVG must appear in the rendered HTML so
        that the sparkline is visible to the operator.
        """
        obs = _make_phase36_obs(obs_id=601)

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-36:601")

        assert resp.status_code == 200, (
            f"GET returned {resp.status_code} for new-row obs with equity_curve_downsampled. "
            "SR-1: route must return 200 for new-format rows."
        )

        html = resp.get_data(as_text=True)
        assert "<svg" in html, (
            "Rendered HTML does not contain '<svg' for a new-row obs with "
            "equity_curve_downsampled=[0.0, 0.5, 1.2, ...] (20 points). "
            "SR-1: the render_sparkline macro must emit an inline <svg> element when "
            "sparkline_points is a non-empty list of numbers."
        )

    def test_new_row_svg_has_aria_label(self, client):
        """SR-2: The rendered SVG has an aria-label attribute for accessibility (AC-3).

        Screen readers need an aria-label to describe the sparkline chart to users
        who cannot see it. The macro must include aria-label on the <svg> element.
        """
        obs = _make_phase36_obs(obs_id=602)

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-36:602")

        html = resp.get_data(as_text=True)
        assert "aria-label" in html, (
            "Rendered HTML does not contain 'aria-label' for a new-row sparkline SVG. "
            "SR-2 / AC-3: the <svg> element must have an aria-label attribute for "
            "screen-reader accessibility. Missing aria-label makes the sparkline "
            "invisible to assistive technology."
        )

    def test_new_row_svg_stroke_uses_theme_var(self, client):
        """SR-3: The SVG polyline stroke uses CSS variable var(--studio-accent).

        The sparkline must adapt to the dashboard's colour theme. Using a hardcoded
        hex colour would break dark-mode or rebrand. The macro must set
        stroke='var(--studio-accent)' (or equivalent CSS var reference) on the polyline.
        """
        obs = _make_phase36_obs(obs_id=603)

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-36:603")

        html = resp.get_data(as_text=True)
        assert "var(--studio-accent)" in html, (
            "Rendered HTML does not contain 'var(--studio-accent)' for the sparkline stroke. "
            "SR-3: the SVG polyline stroke must use the CSS variable var(--studio-accent) "
            "to stay theme-aware. A hardcoded colour breaks dark-mode and rebranding."
        )

    def test_hr4_no_svg_when_points_contains_none(self, client):
        """SR-4: SVG macro skips render when any point in sparkline_points is None (HR-4).

        HR-4: defence-in-depth against None values that may survive _sanitize_non_finite
        (e.g. None was already in the stored list, not introduced as NaN/inf).
        The macro must guard with an all-numeric check: if any element is not a number,
        do not render the SVG (to avoid NaN-coordinate SVG paths in the browser).
        """
        obs = _make_phase36_obs(obs_id=604, sparkline=[0.0, None, 1.5])

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-36:604")

        html = resp.get_data(as_text=True)
        assert "<svg" not in html, (
            "Rendered HTML contains '<svg' for obs with sparkline_points=[0.0, None, 1.5]. "
            "HR-4: the macro must skip render when any point is not a number (None check). "
            "A None value in the points list would produce an invalid SVG coordinate."
        )

    def test_hr4_no_svg_when_points_is_empty_list(self, client):
        """SR-5: SVG macro skips render when sparkline_points is an empty list (HR-4).

        An empty list is falsy in Jinja2 so ``{% if points %}`` evaluates to False.
        No SVG element must be emitted — not even an empty <svg></svg> stub.
        """
        obs = _make_phase36_obs(obs_id=605, sparkline=[])

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-36:605")

        html = resp.get_data(as_text=True)
        assert "<svg" not in html, (
            "Rendered HTML contains '<svg' for obs with sparkline_points=[] (empty list). "
            "HR-4 / HR-1: the macro must not render any SVG element when points is empty. "
            "An empty list is falsy; the '{% if points %}' guard must prevent any SVG output."
        )

    def test_withheld_card_also_renders_svg_sparkline(self, client):
        """SR-6: A WITHHELD (rejected) obs with equity_curve_downsampled also renders <svg>.

        [PM-ASSUMED §2]: rejected candidates get sparklines too — the sparkline is
        candidate-scoped, not survivor-scoped. The macro must be called in the
        withheld/rejected card section of the template, not only on survivor cards.
        """
        obs = _make_phase36_obs(obs_id=606, sparkline=[0.0, 0.3, 0.7])
        obs = dict(obs)
        obs["verdict"] = "WITHHELD_FDR"
        obs["raw_response"] = dict(obs["raw_response"])
        obs["raw_response"]["gate_decision"] = "WITHHELD_FDR"

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-36:606")

        assert resp.status_code == 200, (
            f"GET returned {resp.status_code} for WITHHELD obs with equity_curve_downsampled. "
            "SR-6: withheld-card route must return 200."
        )

        html = resp.get_data(as_text=True)
        assert "<svg" in html, (
            "Rendered HTML does not contain '<svg' for a WITHHELD obs with "
            "equity_curve_downsampled=[0.0, 0.3, 0.7]. "
            "SR-6 / [PM-ASSUMED]: rejected/withheld cards must also render the sparkline. "
            "The render_sparkline macro must be called in the withheld-card section."
        )
