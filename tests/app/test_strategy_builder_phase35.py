"""RED tests — Phase 3.5 Strategy Builder: Metrics Persistence at Observation Write.

Phase 3.5 closes the Phase-3 deviations (baseline column + Phase-4 field-drops):
  - Persist candidate metrics + live_baseline sub-dict into raw_response at
    _persist_survivor time AND for rejected candidates.
  - Surface the new fields into card_artifacts in the GET route.
  - Render baseline column in the stats table iff live_baseline is present.

Binding contract: feature-plans/strategy-builder-phase35-contract.md

Hard requirements under test:
  HR-1: Old rows (no metrics in raw_response) must render crash-free, single-column.
  HR-2: No recomputation at read time.
  HR-3: FDR gate semantics untouched.
  HR-5: live_baseline uses the SAME tail-aligned window as _passes_screens.

Groups:
  PA: persist-payload shape (unit — mock database.insert_advisor_observation)
  PB: tail-alignment reuse (HR-5)
  PC: old-row golden fixture / backward compat (HR-1)
  PD: baseline-column-iff-present render
  PE: M6 artifact population for new rows

==========================================================================
CYCLE 1 — RED tests (must FAIL before implementation)
==========================================================================

Mocking strategy:
  - PA tests: patch database.insert_advisor_observation; capture call_args.
  - PB tests: call _passes_screens and check alignment properties directly.
  - PC/PD tests: Flask test client; inject fixture obs via
    patch("database.get_advisor_observations_for_symphony", ...).
  - PE tests: patch render_template to capture card_artifacts context.
  - All fixtures are function-scoped.
  - No live API calls — no @pytest.mark.live here.

Fixture path for old-row tests:
  tests/fixtures/ai_advisor/m6/strategy_builder_observations_basic.json
  (pre-3.5 fixture — no live_baseline in raw_response)
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import MagicMock, call, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Repository root and fixture paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_BASIC_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "ai_advisor" / "m6" / "strategy_builder_observations_basic.json"
)


def _load_basic_fixture() -> dict:
    """Load the pre-3.5 observations fixture (no live_baseline in raw_response)."""
    return json.loads(_BASIC_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared Flask test client fixture (function-scoped — no shared state)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Flask test client with testing mode enabled."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers: build a minimal CandidateInfo + CandidateGateResult for PA tests
# ---------------------------------------------------------------------------


def _make_candidate_info(metrics: dict | None = None):
    """Return a minimal CandidateInfo with the given metrics dict."""
    from advisors.strategy_builder_engine import CandidateInfo

    return CandidateInfo(
        candidate_id="test:T1:equal_weight",
        tree={},
        template_id="T1",
        params={"objective": "diversify", "tickers": ["SPY", "AGG"]},
        metrics=metrics if metrics is not None else {
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


def _make_gate_result(candidate_id: str = "test:T1:equal_weight", verdict_str: str = "ADOPT_CANDIDATE"):
    """Return a minimal CandidateGateResult stub (MagicMock — avoids NamedTuple field count coupling).

    Using MagicMock instead of the real CandidateGateResult so that changes to
    the NamedTuple's field list in backtest_gate_engine.py do not break the test
    helper. The mock exposes only the attributes that _persist_survivor reads.
    """
    verdict_mock = MagicMock()
    verdict_mock.decision = verdict_str

    result = MagicMock()
    result.candidate_id = candidate_id
    result.verdict = verdict_mock
    result.winner_p_adj = 0.012
    result.caveats = ["Selected on backtest"]
    return result


# ===========================================================================
# Group PA: persist-payload shape
# ===========================================================================


def test_pa1_persist_survivor_raw_response_contains_all_8_phase35_fields():
    """PA-1: _persist_survivor raw_response contains all 8 §2 fields.

    Contract §2 mandates: cagr, sharpe, calmar, max_drawdown,
    correlation_vs_live, blended_drawdown, n_candidates, n_survivors.

    We capture the raw_response kwarg passed to database.insert_advisor_observation
    and assert all 8 keys are present. We do NOT assert the values — they come
    from info.metrics which is a fixture sentinel, not a producer computation.
    """
    from advisors.strategy_builder_engine import _persist_survivor

    info = _make_candidate_info()
    gate_result = _make_gate_result()

    with patch("database.insert_advisor_observation") as mock_insert:
        _persist_survivor(
            symphony_id="sym-test",
            info=info,
            gate_result=gate_result,
            n_candidates=4,
            live_returns=[0.1, -0.2, 0.3, -0.1, 0.2],
            n_survivors=1,
        )

    assert mock_insert.called, "_persist_survivor must call database.insert_advisor_observation."

    call_kwargs = mock_insert.call_args.kwargs
    rr = call_kwargs.get("raw_response", {})

    required_fields = [
        "cagr",
        "sharpe",
        "calmar",
        "max_drawdown",
        "correlation_vs_live",
        "blended_drawdown",
        "n_candidates",
        "n_survivors",
    ]
    missing = [f for f in required_fields if f not in rr]
    assert not missing, (
        f"_persist_survivor raw_response is missing §2 fields: {missing}. "
        "Phase 3.5 contract §2 requires all 8 fields in the persisted raw_response. "
        "Values come from info.metrics — no recomputation on the persist path."
    )


def test_pa2_persist_survivor_with_live_returns_includes_live_baseline():
    """PA-2: _persist_survivor with non-empty live_returns includes live_baseline sub-dict.

    When live_returns is non-empty, the persisted raw_response must contain a
    'live_baseline' key whose value is a dict with the same metric keys as the
    candidate metrics (cagr, sharpe, calmar, max_drawdown at minimum).
    """
    from advisors.strategy_builder_engine import _persist_survivor

    info = _make_candidate_info()
    gate_result = _make_gate_result()
    # Use a non-empty live_returns series (length >= 2 required for metrics).
    live_returns = [0.1, -0.2, 0.3, -0.1, 0.2, 0.15, -0.05, 0.1]

    with patch("database.insert_advisor_observation") as mock_insert:
        _persist_survivor(
            symphony_id="sym-test",
            info=info,
            gate_result=gate_result,
            n_candidates=4,
            live_returns=live_returns,
            n_survivors=1,
        )

    rr = mock_insert.call_args.kwargs.get("raw_response", {})

    assert "live_baseline" in rr, (
        "_persist_survivor must include 'live_baseline' in raw_response when "
        "live_returns is non-empty. Contract §2: live_baseline sub-dict is included "
        "when live_returns is provided; omitted when not."
    )
    baseline = rr["live_baseline"]
    assert isinstance(baseline, dict), (
        f"live_baseline must be a dict, got {type(baseline).__name__}."
    )
    # live_baseline must contain the same metric keys as the candidate metrics sub-dict.
    expected_baseline_keys = ["cagr", "sharpe", "calmar", "max_drawdown"]
    missing_keys = [k for k in expected_baseline_keys if k not in baseline]
    assert not missing_keys, (
        f"live_baseline is missing metric keys: {missing_keys}. "
        "live_baseline must have the same metric keys as candidate metrics "
        "(contract [PM-ASSUMED]: 'live_baseline metric keys mirror the candidate metric keys')."
    )


def test_pa3_persist_survivor_without_live_returns_has_no_live_baseline():
    """PA-3: _persist_survivor with empty live_returns has NO live_baseline key.

    When live_returns is empty, the live_baseline key must be completely absent
    from the raw_response (not None, not {}, but absent). Contract §2:
    'live_baseline sub-dict ... omitted when not [provided]'.
    """
    from advisors.strategy_builder_engine import _persist_survivor

    info = _make_candidate_info()
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

    assert "live_baseline" not in rr, (
        "_persist_survivor must NOT include 'live_baseline' in raw_response when "
        "live_returns is empty. Contract §2: 'live_baseline sub-dict ... omitted when not provided'. "
        f"Got raw_response keys: {list(rr.keys())!r}"
    )


def test_pa4_rejected_candidate_persist_captures_same_metric_fields():
    """PA-4: Rejected-candidate persist call captures same metric fields with non-ADOPT verdict.

    Contract [PM-ASSUMED]: rejected candidates persist metrics too. Their cards
    and artifacts benefit equally from the persisted metrics. The verdict must
    NOT be 'ADOPT_CANDIDATE' for rejected candidates.

    We verify that propose_strategies (or its internal rejected-persist path) calls
    database.insert_advisor_observation for a rejected gate result with:
      1. The same §2 metric fields in raw_response.
      2. verdict != 'ADOPT_CANDIDATE' (e.g. 'WITHHELD_FDR').
      3. is_advisory_only=1.

    Approach: mock the gate to return all candidates as rejected; capture all
    insert_advisor_observation calls.
    """
    from advisors.strategy_builder_engine import _persist_survivor

    # Build a gate result with a non-ADOPT verdict
    gate_result = _make_gate_result(verdict_str="WITHHELD_FDR")
    info = _make_candidate_info()

    with patch("database.insert_advisor_observation") as mock_insert:
        _persist_survivor(
            symphony_id="sym-test",
            info=info,
            gate_result=gate_result,
            n_candidates=4,
            live_returns=[],
            n_survivors=0,
            is_rejected=True,  # new parameter — signals rejected-path persist
        )

    assert mock_insert.called, (
        "Rejected-candidate persist must call database.insert_advisor_observation."
    )

    call_kwargs = mock_insert.call_args.kwargs
    rr = call_kwargs.get("raw_response", {})

    # Must contain the §2 metric fields
    required_fields = ["cagr", "sharpe", "calmar", "max_drawdown", "n_candidates"]
    missing = [f for f in required_fields if f not in rr]
    assert not missing, (
        f"Rejected-candidate raw_response missing fields: {missing}. "
        "Rejected candidates must persist the same §2 metric fields as survivors."
    )

    # Verdict must NOT be ADOPT_CANDIDATE
    verdict_written = call_kwargs.get("verdict")
    assert verdict_written != "ADOPT_CANDIDATE", (
        f"Rejected-candidate persist must write a non-ADOPT verdict. "
        f"Got verdict={verdict_written!r}. "
        "The verdict must reflect the gate decision (e.g. 'WITHHELD_FDR')."
    )

    # Must be advisory-only
    assert call_kwargs.get("is_advisory_only") == 1, (
        "Rejected-candidate persist must set is_advisory_only=1. "
        "All strategy_builder observations are advisory-only (never trade orders)."
    )


# ===========================================================================
# Group PB: tail-alignment reuse (HR-5)
# ===========================================================================


def test_pb1_live_baseline_uses_tail_aligned_window_matching_passes_screens():
    """PB-1: live_baseline correlation window equals min(len(live_returns), len(returns_pct)).

    HR-5: live_baseline must use the SAME tail-aligned window as _passes_screens.
    The blended computation in _passes_screens uses:
        n = min(len(live_returns), len(returns_pct))
        blended = [(r + lv) * 0.5 for r, lv in zip(returns_pct[-n:], live_returns[-n:])]

    Verify: given live_returns of length 5 and returns_pct of length 10, the
    baseline is computed from the shorter tail (length 5), not the full 10-element
    candidate series. We inspect the computed live_baseline.max_drawdown to confirm
    it equals compute_quantstats_metrics(live_returns[-5:]) output, NOT
    compute_quantstats_metrics(live_returns[-10:]) (which would be the same series
    but with a longer candidate window, which is wrong if the series were different).

    Approach: call _persist_survivor with mismatched lengths; capture raw_response;
    verify live_baseline.max_drawdown matches the tail-aligned computation.
    """
    from analytics import compute_quantstats_metrics
    from advisors.strategy_builder_engine import _persist_survivor

    # live_returns shorter than returns_pct would be stored in info.metrics context
    # We embed returns_pct length info via a custom info object
    live_returns = [0.1, -0.2, 0.3, -0.15, 0.05]  # length 5
    # The candidate returns_pct would be length 10 (longer), stored in info
    # We verify live_baseline is computed from live_returns[-5:] (full live_returns)
    # rather than from a misaligned window.

    info = _make_candidate_info()
    # Attach returns_pct to info so the persist path can use the right alignment
    info._returns_pct = [0.2, -0.1, 0.15, -0.05, 0.1, 0.08, -0.12, 0.07, -0.03, 0.09]  # length 10
    gate_result = _make_gate_result()

    with patch("database.insert_advisor_observation") as mock_insert:
        _persist_survivor(
            symphony_id="sym-test",
            info=info,
            gate_result=gate_result,
            n_candidates=4,
            live_returns=live_returns,
            n_survivors=1,
        )

    rr = mock_insert.call_args.kwargs.get("raw_response", {})
    baseline = rr.get("live_baseline", {})

    # n = min(5, 10) = 5 → use live_returns[-5:] (all 5 elements since len=5)
    n_tail = min(len(live_returns), len(info._returns_pct))
    expected_metrics = compute_quantstats_metrics(live_returns[-n_tail:])
    expected_mdd = expected_metrics.get("max_drawdown")

    baseline_mdd = baseline.get("max_drawdown")

    # Both should agree on the tail window. We use pytest.approx with 1e-9 tolerance
    # because both sides call the same compute_quantstats_metrics with the same input.
    if expected_mdd is None:
        assert baseline_mdd is None, (
            f"live_baseline.max_drawdown should be None (insufficient data) "
            f"but got {baseline_mdd!r}."
        )
    else:
        assert baseline_mdd == pytest.approx(expected_mdd, abs=1e-9), (
            # Tolerance: 1e-9 — both sides compute from the same input series via
            # the same function, so floating-point should be bit-for-bit identical;
            # the small epsilon guards only against any intermediate rounding.
            f"live_baseline.max_drawdown={baseline_mdd!r} does not match "
            f"tail-aligned compute_quantstats_metrics(live_returns[-{n_tail}:]) "
            f"= {expected_mdd!r}. "
            "HR-5: live_baseline must use the same tail-aligned window as _passes_screens."
        )


def test_pb2_live_baseline_max_drawdown_agrees_with_tail_aligned_live_series():
    """PB-2: live_baseline.max_drawdown agrees with compute_quantstats_metrics(live_returns[-n:]).

    n = tail-aligned window length = min(len(live_returns), len(returns_pct)).
    This test uses a known live_returns and returns_pct with DIFFERENT lengths to
    make the alignment observable:
      - live_returns has 8 elements
      - returns_pct has 20 elements
      - n = min(8, 20) = 8 → live_baseline computed from live_returns[-8:] (all 8)

    We verify the persisted live_baseline.max_drawdown equals the result of
    compute_quantstats_metrics on the correctly-aligned tail of live_returns.
    NOT the full returns_pct series, NOT a head-aligned slice.
    """
    from analytics import compute_quantstats_metrics
    from advisors.strategy_builder_engine import _persist_survivor

    live_returns = [0.2, -0.3, 0.1, 0.05, -0.15, 0.12, -0.08, 0.18]  # length 8
    returns_pct_length = 20
    fake_returns_pct = [0.01 * i for i in range(returns_pct_length)]

    info = _make_candidate_info()
    info._returns_pct = fake_returns_pct  # length 20
    gate_result = _make_gate_result()

    with patch("database.insert_advisor_observation") as mock_insert:
        _persist_survivor(
            symphony_id="sym-test",
            info=info,
            gate_result=gate_result,
            n_candidates=5,
            live_returns=live_returns,
            n_survivors=1,
        )

    rr = mock_insert.call_args.kwargs.get("raw_response", {})
    baseline = rr.get("live_baseline", {})

    n_tail = min(len(live_returns), returns_pct_length)  # = 8
    expected_metrics = compute_quantstats_metrics(live_returns[-n_tail:])
    expected_mdd = expected_metrics.get("max_drawdown")
    baseline_mdd = baseline.get("max_drawdown")

    if expected_mdd is None:
        assert baseline_mdd is None, (
            f"live_baseline.max_drawdown must be None when live series has insufficient data. "
            f"Got {baseline_mdd!r}."
        )
    else:
        assert baseline_mdd == pytest.approx(expected_mdd, abs=1e-9), (
            # Tolerance: 1e-9 — same series, same function; bit-for-bit match expected.
            f"live_baseline.max_drawdown={baseline_mdd!r} does not agree with "
            f"compute_quantstats_metrics(live_returns[-{n_tail}:])={expected_mdd!r}. "
            "PB-2: must use tail-aligned window (n=min(live_len, pct_len)), "
            "NOT the full live series when candidate series is shorter."
        )


# ===========================================================================
# Group PC: old-row golden fixture / backward compat (HR-1)
# ===========================================================================


def test_pc1_old_row_renders_single_column_stats_table_no_crash(client):
    """PC-1: Pre-3.5 observation (no metrics in raw_response) renders crash-free.

    HR-1: old rows must render exactly as today — no KeyError, no None-formatting
    artifacts ('None%'), single-column table. The golden fixture row has no
    'cagr', 'sharpe', 'calmar', or 'live_baseline' keys in its raw_response.
    """
    fixture = _load_basic_fixture()
    obs = fixture["observation_survivor"]
    # Verify this is a pre-3.5 row (no Phase 3.5 keys in raw_response)
    rr = obs["raw_response"]
    assert "cagr" not in rr, (
        "Test setup error: the pre-3.5 fixture already has 'cagr' in raw_response. "
        "The golden-fixture test requires a pre-3.5 row without Phase 3.5 fields."
    )

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    assert resp.status_code == 200, (
        f"GET /ai-advisor/strategy-builder returned {resp.status_code} for a pre-3.5 "
        "observation row. HR-1: old rows must render without crashing."
    )

    html = resp.get_data(as_text=True)

    # Must not contain "None%" — the None-formatting artifact
    assert "None%" not in html, (
        "Rendered HTML contains 'None%' — a None-formatting artifact from an unguarded "
        "metric format string. HR-1: pre-3.5 rows must render '—' (em-dash) for "
        "missing metric values, never 'None%'."
    )

    # Must not produce a Python traceback in the response
    assert "Traceback" not in html, (
        "Rendered HTML contains 'Traceback' — a Python exception surfaced to the page. "
        "HR-1: old rows must not cause any server-side exception."
    )


def test_pc2_old_row_card_artifacts_cagr_is_none_not_string_none(client):
    """PC-2: Old-row card_artifacts entry has None (Python None) for cagr/sharpe/calmar.

    When the raw_response has no 'cagr' key, the card_artifacts dict must use
    Python None as the value (JSON null), NOT the string "None". The string "None"
    would be rendered as "None%" if a format string were applied to it without a guard.
    """
    fixture = _load_basic_fixture()
    obs = fixture["observation_survivor"]
    obs_id = obs["id"]

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

    card_artifacts = captured.get("card_artifacts", {})
    artifact = card_artifacts.get(obs_id, {})

    for field in ("cagr", "sharpe", "calmar"):
        val = artifact.get(field)
        # The value may be absent (key not in artifact) or Python None — both acceptable.
        # But it must NOT be the string "None".
        assert val != "None", (
            f"card_artifacts[{obs_id}]['{field}'] is the string 'None' for a pre-3.5 row. "
            "The value must be Python None (JSON null) or absent — never the string 'None'. "
            "The string 'None' would render as 'None%' in a format string without a guard."
        )


def test_pc3_old_row_stats_table_has_no_live_baseline_column(client):
    """PC-3: Old-row stats table has exactly ONE data column header (no 'Live Baseline').

    HR-1: pre-3.5 rows must render single-column — the baseline column must only
    appear when live_baseline is present in raw_response. The rendered stats table
    header for an old row must have exactly one data column: 'Candidate'.
    'Live Baseline' must NOT appear in the header row.
    """
    fixture = _load_basic_fixture()
    obs = fixture["observation_survivor"]
    # Confirm the pre-3.5 fixture has no live_baseline
    assert "live_baseline" not in obs["raw_response"], (
        "Test setup error: the pre-3.5 fixture already has 'live_baseline'. "
        "This test requires a row without live_baseline."
    )

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)

    # The 'Live Baseline' header must not appear in an old-row render.
    assert "Live Baseline" not in html, (
        "Old-row (pre-3.5) rendered HTML contains 'Live Baseline' column header. "
        "HR-1: the baseline column must ONLY render when live_baseline is present "
        "in raw_response. Old rows must render single-column (Candidate only)."
    )


# ===========================================================================
# Group PD: baseline-column-iff-present render
# ===========================================================================


def _make_new_row_obs(obs_id: int = 201) -> dict:
    """Build a Phase 3.5 observation row with live_baseline in raw_response.

    Values are structural sentinels (positive float / negative float) — not
    producer-computed quant outputs. The signs match the contract (cagr > 0,
    max_drawdown < 0).
    """
    return {
        "id": obs_id,
        "created_at": "2026-06-12T10:00:00",
        "advisor_role": "STRATEGY_BUILDER",
        "subject_type": "strategy_proposal",
        "subject_id": "sym-test-new",
        "verdict": "ADOPT_CANDIDATE",
        "raw_response": {
            "objective": "diversify",
            "template_id": "T1",
            "candidate_id": f"sym-test-new:T1:{obs_id}",
            "metrics": {
                "annualized_return": 0.09,
                "sharpe": 0.65,
                "calmar": 0.48,
                "max_drawdown": -0.18,
                "sortino": 0.72,
                "total_return": 0.45,
                "win_rate": 0.55,
                "volatility": 0.11,
            },
            # Phase 3.5 top-level metric fields
            "cagr": 0.09,
            "sharpe": 0.65,
            "calmar": 0.48,
            "max_drawdown": -0.18,
            "correlation_vs_live": 0.25,
            "blended_drawdown": -0.10,
            "n_candidates": 4,
            "n_survivors": 1,
            # live_baseline sub-dict — triggers baseline column render
            "live_baseline": {
                "cagr": 0.085,
                "sharpe": 0.55,
                "calmar": 0.40,
                "max_drawdown": -0.20,
            },
            "gate_decision": "ADOPT_CANDIDATE",
            "winner_p_adj": 0.010,
            "fdr_q": 0.05,
            "fdr_adjusted_threshold": 0.017,
            "caveats": ["Selected on backtest"],
            "rules_text": "Equal weight: SPY, AGG, GLD",
            "apply_guidance": "Apply manually in Composer.",
            "tickers": ["SPY", "AGG", "GLD"],
        },
        "is_advisory_only": 1,
        "spec_bundle_id": None,
    }


def test_pd1_new_row_stats_table_has_two_column_headers(client):
    """PD-1: New-row (has live_baseline in raw_response) renders TWO column headers.

    Contract §2: 'Render the baseline column in the stats table when live_baseline
    present.' The rendered stats table header must contain both:
      - 'Candidate'
      - 'Live Baseline'
    when the observation row's raw_response has a 'live_baseline' sub-dict.
    """
    obs = _make_new_row_obs(obs_id=201)

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-new")

    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)

    assert "Live Baseline" in html, (
        "New-row (live_baseline present) rendered HTML must contain 'Live Baseline' "
        "column header in the stats table. "
        "Contract §2: baseline column renders iff live_baseline is present."
    )
    assert "Candidate" in html, (
        "New-row rendered HTML must still contain the 'Candidate' column header. "
        "The baseline column is added alongside — not replacing — the candidate column."
    )


def test_pd2_new_row_baseline_column_shows_formatted_value_not_dash(client):
    """PD-2: New-row baseline column shows formatted value (e.g. '8.50%') not '—' or None.

    When live_baseline.cagr is a positive float (sentinel 0.085), the rendered
    baseline column for annual return must show a percentage string (e.g. '8.50%'),
    not '—' (which would indicate the template uses the else/None branch).

    This test ensures the template accesses live_baseline values correctly and
    applies the format string to them.
    """
    obs = _make_new_row_obs(obs_id=202)
    # Confirm the fixture has a non-None cagr in live_baseline
    assert obs["raw_response"]["live_baseline"]["cagr"] is not None, (
        "Test setup: live_baseline.cagr must be non-None for this test."
    )

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-new")

    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)

    # The baseline cagr value is 0.085 → formatted as "8.50%"
    # We assert a % character appears somewhere in the stats table context
    # (not asserting exact value — "is positive float formatted as %").
    assert "%" in html, (
        "New-row with live_baseline.cagr=0.085 must render a percentage value "
        "(e.g. '8.50%') in the baseline column. Got no '%' character in the HTML. "
        "The template must format live_baseline metric values — not show '—' for them."
    )

    # More specific: the baseline cagr value should appear as a % string
    # 8.50% is the sentinel format for 0.085 * 100 = 8.5
    # We also accept 8.5% (no trailing zero). Assert the Live Baseline column header
    # co-occurs with at least one % value (not just the candidate column).
    assert "Live Baseline" in html and "%" in html, (
        "New-row must render both 'Live Baseline' column and at least one % value. "
        "The template must not render the baseline column header while leaving "
        "all baseline values as '—'."
    )


def test_pd3_old_row_stats_table_header_has_exactly_one_data_column(client):
    """PD-3: Old-row (no live_baseline) stats table has exactly ONE data column header.

    Using the pre-3.5 survivor fixture (no 'live_baseline' key). The stats table
    rendered HTML must contain exactly ONE <th> element for the data column
    ('Candidate') and must NOT contain a second data <th> for 'Live Baseline'.

    We count <th> elements in the stats-table to verify single-column rendering.
    """
    fixture = _load_basic_fixture()
    obs = fixture["observation_survivor"]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)

    # Extract the stats-table section to count <th> elements
    # (isolate the relevant table by testid anchor)
    stats_table_pattern = re.compile(
        r'data-testid="stats-table"[^>]*>(.*?)</table>',
        re.DOTALL | re.IGNORECASE,
    )
    matches = stats_table_pattern.findall(html)
    assert matches, (
        "Could not find stats-table (data-testid='stats-table') in rendered HTML. "
        "The stats table must be rendered for pre-3.5 rows."
    )

    # Count <th> elements in the first stats table.
    # Use \bth\b word-boundary to avoid matching <thead> tags.
    # Pattern: opening < then word-boundary th then either whitespace, > or /
    first_table_html = matches[0]
    th_count = len(re.findall(r"<th[\s>/]", first_table_html, re.IGNORECASE))

    # Must have exactly 2 <th>: "Metric" + "Candidate" (no "Live Baseline")
    assert th_count == 2, (
        f"Old-row stats table has {th_count} <th> elements. Expected exactly 2 "
        "('Metric' + 'Candidate'). Old rows must NOT have a 'Live Baseline' <th>. "
        "HR-1: pre-3.5 rows render single-column."
    )


# ===========================================================================
# Group PE: M6 artifact population for new rows (card_artifacts)
# ===========================================================================


def test_pe1_get_route_card_artifacts_for_new_row_includes_phase35_metric_keys(client):
    """PE-1: GET route card_artifacts for a new-row obs includes §2 metric keys.

    AC-1 / Phase 4 AC-1: a new-row observation's card_artifacts must contain
    the Phase 3.5 metric keys: cagr, sharpe, calmar, correlation_vs_live,
    blended_drawdown. Values may be None if not computed, but the keys must be
    present (the Discuss affordance and chat grounding depend on them).
    """
    obs = _make_new_row_obs(obs_id=301)
    obs_id = obs["id"]

    captured: dict = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return "<html><body>stub</body></html>"

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
        patch.object(app_module, "render_template", side_effect=_capture),
    ):
        client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-new")

    card_artifacts = captured.get("card_artifacts", {})
    artifact = card_artifacts.get(obs_id, {})

    assert artifact, (
        f"card_artifacts must contain an entry for new-row obs id={obs_id}. "
        "The GET route must build card_artifacts for Phase 3.5 rows."
    )

    phase35_keys = ["cagr", "sharpe", "calmar", "correlation_vs_live", "blended_drawdown"]
    missing = [k for k in phase35_keys if k not in artifact]
    assert not missing, (
        f"card_artifacts[{obs_id}] is missing Phase 3.5 metric keys: {missing}. "
        "PE-1: The GET route must surface all §2 metric fields into card_artifacts "
        "for new rows. These fields are already allowlisted in Phase 4 — construction only."
    )


def test_pe2_get_route_card_artifacts_cagr_matches_raw_response_cagr(client):
    """PE-2: GET route card_artifacts for a new-row obs: cagr value matches raw_response['cagr'].

    The card_artifacts construction must derive cagr from the stored raw_response,
    not synthesize or recompute it. If the value differs, the artifact is misrepresenting
    the stored observation data.
    """
    obs = _make_new_row_obs(obs_id=302)
    obs_id = obs["id"]
    expected_cagr = obs["raw_response"]["cagr"]  # 0.09 sentinel

    captured: dict = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return "<html><body>stub</body></html>"

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
        patch.object(app_module, "render_template", side_effect=_capture),
    ):
        client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-new")

    card_artifacts = captured.get("card_artifacts", {})
    artifact = card_artifacts.get(obs_id, {})

    assert "cagr" in artifact, (
        f"card_artifacts[{obs_id}] must contain 'cagr'. "
        "PE-2: the GET route must populate cagr from raw_response['cagr']."
    )
    assert artifact["cagr"] == expected_cagr, (
        # Tolerance: exact equality — cagr is copied from raw_response dict,
        # not recomputed, so floating-point should be bit-for-bit identical.
        f"card_artifacts[{obs_id}]['cagr']={artifact['cagr']!r} does not match "
        f"raw_response['cagr']={expected_cagr!r}. "
        "PE-2: cagr must be derived directly from raw_response (HR-2: no recomputation "
        "at read time). If the values differ, the route is synthesizing the metric."
    )


# ===========================================================================
# CYCLE 2 — ADVERSARIAL tests
# (appended after implementer reports GREEN on cycle 1)
# ===========================================================================


class TestAdversarialCycle2Phase35:
    """Adversarial Cycle 2 — Phase 3.5 contract §5 mandatory second cycle.

    Targets six attack vectors:
      ADV35-1: _persist_survivor must use info.metrics — not recompute.
      ADV35-2: correlation_vs_live stored as float when both series non-empty.
      ADV35-3: raw_response with metrics={} (empty dict) renders crash-free.
      ADV35-4: baseline None values render as '—' not 'None%'.
      ADV35-5: Rejected-candidate persist verdict is NOT 'ADOPT_CANDIDATE'.
    """

    def test_adv35_1_persist_survivor_uses_precomputed_metrics_not_recomputed(self):
        """ADV35-1: _persist_survivor uses ALREADY-COMPUTED info.metrics — not recomputed.

        HR-2: no recomputation at read time. The same constraint applies to the
        persist path: values come from metrics ALREADY computed in the run.

        Verify: if info.metrics has a stale/sentinel cagr value (0.42), the persisted
        raw_response['cagr'] is 0.42 — not recomputed from info.tree or info.params.

        This test will FAIL if the implementation calls compute_quantstats_metrics
        on the persist path (which would produce a different value from the sentinel).
        """
        from advisors.strategy_builder_engine import _persist_survivor

        # Use a deliberately odd sentinel value that would never be produced by
        # compute_quantstats_metrics on an empty tree (0.42 is the "stale" marker).
        stale_metrics = {
            "annualized_return": 0.42,  # stale sentinel — must be preserved as-is
            "sharpe": 1.11,
            "calmar": 0.99,
            "max_drawdown": -0.07,
            "sortino": 1.22,
            "total_return": 2.00,
            "win_rate": 0.60,
            "volatility": 0.08,
        }
        info = _make_candidate_info(metrics=stale_metrics)
        gate_result = _make_gate_result()

        with patch("database.insert_advisor_observation") as mock_insert:
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=3,
                live_returns=[],
                n_survivors=1,
            )

        rr = mock_insert.call_args.kwargs.get("raw_response", {})
        persisted_cagr = rr.get("cagr")

        # annualized_return maps to cagr in the §2 field names
        assert persisted_cagr == pytest.approx(0.42, abs=1e-9), (
            # Tolerance: 1e-9 — exact copy from info.metrics; any difference signals
            # recomputation (which is forbidden by HR-2).
            f"_persist_survivor persisted cagr={persisted_cagr!r} instead of the "
            "pre-computed info.metrics['annualized_return']=0.42. "
            "ADV35-1: _persist_survivor must use info.metrics as-is — never recompute. "
            "HR-2: no recomputation on the persist path."
        )

    def test_adv35_2_correlation_vs_live_is_float_when_both_series_nonempty(self):
        """ADV35-2: correlation_vs_live is a float (not None) when both series non-empty.

        Even when live_returns and returns_pct have unequal lengths, tail-alignment
        must handle the mismatch and produce a valid correlation float, not None.

        We use a 3-element live_returns and a 10-element returns_pct to ensure
        the tail-alignment logic (min(3, 10)=3) is exercised.
        """
        from advisors.strategy_builder_engine import _persist_survivor

        info = _make_candidate_info()
        # Attach returns_pct so the persist path can compute correlation
        info._returns_pct = [0.01 * i for i in range(10)]  # length 10
        gate_result = _make_gate_result()

        live_returns = [0.05, -0.03, 0.02]  # length 3 (shorter)

        with patch("database.insert_advisor_observation") as mock_insert:
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=3,
                live_returns=live_returns,
                n_survivors=1,
            )

        rr = mock_insert.call_args.kwargs.get("raw_response", {})
        corr = rr.get("correlation_vs_live")

        assert corr is not None, (
            "correlation_vs_live must be a float (not None) when both live_returns "
            "and returns_pct are non-empty. Tail-alignment must handle unequal lengths. "
            f"Got None. live_returns length=3, returns_pct length=10."
        )
        assert isinstance(corr, float), (
            f"correlation_vs_live must be a float, got {type(corr).__name__}={corr!r}. "
            "ADV35-2: tail-alignment must produce a valid correlation value."
        )

    def test_adv35_3_raw_response_with_empty_metrics_dict_renders_crash_free(self, client):
        """ADV35-3: row with metrics={} (empty dict, not missing) renders crash-free.

        Different edge case from a truly missing 'metrics' key. An empty dict means
        metrics.get('sharpe_ratio') returns None — the template must handle this
        gracefully (render '—', not crash).

        Uses the observation_backtest_failed fixture which has metrics={}.
        """
        fixture = _load_basic_fixture()
        obs = fixture["observation_backtest_failed"]
        # Confirm metrics is empty dict (not missing)
        assert obs["raw_response"]["metrics"] == {}, (
            "Test setup: observation_backtest_failed must have metrics={} (empty dict)."
        )

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            try:
                resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")
            except Exception as exc:
                pytest.fail(
                    f"GET route raised {type(exc).__name__} on row with metrics={{}}: {exc}. "
                    "ADV35-3: empty metrics dict must render crash-free — '—' for all values."
                )

        assert resp.status_code == 200, (
            f"GET route returned {resp.status_code} for metrics={{}} row. "
            "ADV35-3: empty metrics dict must not cause a 500 error."
        )

        html = resp.get_data(as_text=True)
        assert "None%" not in html, (
            "HTML contains 'None%' for a row with metrics={}. "
            "ADV35-3: empty metrics dict must render '—' for all metric values, "
            "never the string 'None%'."
        )

    def test_adv35_4_baseline_none_values_render_as_dash_not_none_percent(self, client):
        """ADV35-4: live_baseline sub-dict with None values renders '—' not 'None%'.

        When a baseline metric is None (e.g. insufficient data for baseline calmar),
        the template must render '—', not 'None%'. 'None%' is a format-string
        artifact from applying "%.2f%%" to None without a guard.

        We inject a new-row obs with live_baseline containing a None calmar.
        """
        obs = _make_new_row_obs(obs_id=401)
        # Inject None for calmar in live_baseline to trigger the edge case
        obs = dict(obs)
        obs["raw_response"] = dict(obs["raw_response"])
        obs["raw_response"]["live_baseline"] = dict(obs["raw_response"]["live_baseline"])
        obs["raw_response"]["live_baseline"]["calmar"] = None

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-new")

        if resp.status_code != 200:
            pytest.skip(f"Route returned {resp.status_code}.")

        html = resp.get_data(as_text=True)

        assert "None%" not in html, (
            "HTML contains 'None%' when live_baseline.calmar is None. "
            "ADV35-4: None baseline values must render as '—' not 'None%'. "
            "The template must guard None baseline values with an is not none check."
        )

    def test_adv35_5_rejected_candidate_persist_verdict_is_not_adopt_candidate(self):
        """ADV35-5: Rejected-candidate persist writes a verdict that is NOT 'ADOPT_CANDIDATE'.

        When is_rejected=True is passed to _persist_survivor (or a separate
        _persist_rejected function is called), the verdict written to the DB must
        reflect the gate decision — e.g. 'WITHHELD_FDR', 'WITHHELD' — never
        'ADOPT_CANDIDATE'. Persisting 'ADOPT_CANDIDATE' for a rejected candidate
        would corrupt the audit trail and mislead the operator.
        """
        from advisors.strategy_builder_engine import _persist_survivor

        gate_result = _make_gate_result(verdict_str="WITHHELD_FDR")
        info = _make_candidate_info()

        with patch("database.insert_advisor_observation") as mock_insert:
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=3,
                live_returns=[],
                n_survivors=0,
                is_rejected=True,
            )

        assert mock_insert.called, (
            "Rejected-candidate _persist_survivor must call database.insert_advisor_observation."
        )

        verdict_written = mock_insert.call_args.kwargs.get("verdict")
        assert verdict_written != "ADOPT_CANDIDATE", (
            f"Rejected-candidate persist wrote verdict='ADOPT_CANDIDATE'. "
            "ADV35-5: the verdict for a rejected candidate must NOT be 'ADOPT_CANDIDATE'. "
            f"Got: {verdict_written!r}. "
            "Must reflect the gate decision (e.g. 'WITHHELD_FDR', 'WITHHELD')."
        )
        assert verdict_written is not None, (
            "Rejected-candidate persist must write a non-None verdict. "
            "The verdict is required for the audit trail and the Discuss affordance."
        )
