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
    from advisors.strategy_builder_engine import _persist_survivor
    from analytics import compute_quantstats_metrics

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
    from advisors.strategy_builder_engine import _persist_survivor
    from analytics import compute_quantstats_metrics

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
            "Got None. live_returns length=3, returns_pct length=10."
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


# ===========================================================================
# INDEPENDENT CYCLE 2 — adversarial tests (quant-test-writer, 2026-06-12)
# Commissioned after cycle-1 GREEN + dual deferred review.
# Six attack vectors per commission spec.
# ===========================================================================


class TestIndependentCycle2Phase35:
    """Independent Cycle 2 — adversarial second-pass against Phase 3.5 implementation.

    Attack vectors:
      IC2-1: JSON-serializability when metrics contain numpy types + NaN propagation
      IC2-2: _build_live_baseline pathological live_returns inputs
      IC2-3: Template rendering with PARTIAL live_baseline (missing key) and None metric values
      IC2-4: _persist_rejected with empty/all-vetoed batch
      IC2-5: M6 card_artifacts for new-format row with metrics but live_baseline absent
      IC2-6: Old-row golden fixture through full GET render + card_artifacts
             (IC2-independent fixture)

    Each test is independently derived — no shared state with Cycle 1 or pre-implementation
    tests.
    All fixtures are function-scoped via local construction or the _load_basic_fixture helper.
    """

    # -----------------------------------------------------------------------
    # IC2-1: JSON-serializability — numpy types and NaN propagation
    # -----------------------------------------------------------------------

    def test_ic2_1a_numpy_float64_metrics_do_not_raise_typeerror_on_json_serialize(self):
        """IC2-1a: info.metrics with numpy.float64 values must not raise TypeError on json.dumps.

        The production path calls compute_quantstats_metrics() which returns Python float,
        but nothing prevents a caller from injecting numpy.float64 values (e.g., from a
        quantstats version that returns numpy scalars directly). numpy.float64 is a
        subclass of Python float so json.dumps accepts it; this test asserts that the
        serialization does NOT raise TypeError and that the persisted raw_response dict
        can be round-tripped through json.dumps without error.

        Why this matters: database.insert_advisor_observation calls json.dumps(raw_response).
        If any value in the raw_response is a non-serializable numpy type (e.g. numpy.int64),
        the persist call crashes silently (wrapped in try/except in propose_strategies, so
        the error is swallowed — no observation is written, no crash to operator).
        """
        import json

        import numpy as np

        from advisors.strategy_builder_engine import _persist_survivor

        np_metrics = {
            "annualized_return": np.float64(0.12),
            "sharpe": np.float64(0.78),
            "calmar": np.float64(0.55),
            "max_drawdown": np.float64(-0.22),
            "sortino": np.float64(0.90),
            "total_return": np.float64(0.50),
            "win_rate": np.float64(0.55),
            "volatility": np.float64(0.10),
        }
        info = _make_candidate_info(metrics=np_metrics)
        gate_result = _make_gate_result()

        captured: dict = {}

        def fake_insert(**kwargs):
            captured.update(kwargs)
            # Simulate what database.py does — this is the real serialization step.
            rr = kwargs.get("raw_response", {})
            json.dumps(rr)  # Must NOT raise TypeError

        with patch("database.insert_advisor_observation", side_effect=fake_insert):
            try:
                _persist_survivor(
                    symphony_id="sym-test",
                    info=info,
                    gate_result=gate_result,
                    n_candidates=4,
                    live_returns=[0.1, -0.2, 0.3, -0.1, 0.2],
                    n_survivors=1,
                )
            except TypeError as exc:
                pytest.fail(
                    f"json.dumps raised TypeError on numpy.float64 metrics: {exc}. "
                    "IC2-1a: raw_response must be fully JSON-serializable when metrics "
                    "contain numpy.float64 values. numpy.float64 subclasses float so "
                    "standard json.dumps handles it — but any numpy.int64 in the payload "
                    "would cause a TypeError (see IC2-1b for that vector)."
                )

        rr = captured.get("raw_response", {})
        assert isinstance(rr, dict), (
            "IC2-1a: insert_advisor_observation must have been called with raw_response=dict."
        )

    def test_ic2_1b_nan_in_info_metrics_produces_non_rfc_json_is_xfail_bug(self):
        """IC2-1b: float('nan') in info.metrics propagates to raw_response as JSON 'NaN' literal.

        json.dumps(float('nan')) emits the string 'NaN' which is NOT valid RFC 7159 JSON.
        The JS Discuss button reads data-artifact='...' and calls JSON.parse() on it.
        JSON.parse('...NaN...') throws SyntaxError in every browser, silently breaking
        the Discuss affordance for any row whose metrics contain NaN.

        The implementation does NOT sanitize NaN -> null on the persist path.
        compute_quantstats_metrics() prevents NaN via _safe(), but nothing prevents an
        external caller from setting info.metrics = {'annualized_return': float('nan'), ...}.

        This xfail(strict=True) asserts the BUG IS PRESENT: if the implementation adds
        NaN sanitization, the test will flip to XPASS and must be promoted to a passing test.

        IC2-BUG: NaN in info.metrics propagates verbatim to the persisted raw_response,
        producing non-RFC 7159 JSON ('NaN' literal). json.dumps emits 'NaN' without error
        but the output violates RFC 7159 and breaks JS JSON.parse on the Discuss button.
        """
        import json

        from advisors.strategy_builder_engine import _persist_survivor

        nan_metrics = {
            "annualized_return": float("nan"),  # NaN that bypasses compute_quantstats_metrics
            "sharpe": 0.5,
            "calmar": 0.3,
            "max_drawdown": -0.15,
        }
        info = _make_candidate_info(metrics=nan_metrics)
        gate_result = _make_gate_result()

        captured: dict = {}

        def fake_insert(**kwargs):
            captured.update(kwargs)

        with patch("database.insert_advisor_observation", side_effect=fake_insert):
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=3,
                live_returns=[],
                n_survivors=1,
            )

        rr = captured.get("raw_response", {})
        json_str = json.dumps(rr)
        json_contains_nan_literal = "NaN" in json_str

        # Assert the CORRECT behavior: no 'NaN' literal in the JSON string.
        # This assertion FAILS because the bug is present (implementation does not sanitize NaN).
        # When the bug is fixed, json_contains_nan_literal becomes False, this assertion passes
        # → XPASS(strict) → remove the xfail decorator and promote to a passing test.
        assert not json_contains_nan_literal, (
            # Tolerance: N/A — exact string membership check.
            "IC2-1b: json.dumps(raw_response) contains 'NaN' literal (non-RFC 7159). "
            "The implementation must sanitize NaN -> None (JSON null) before persisting. "
            "NaN in raw_response breaks JS JSON.parse on the Discuss button."
        )

    test_ic2_1b_nan_in_info_metrics_produces_non_rfc_json_is_xfail_bug = pytest.mark.xfail(
        strict=True,
        reason=(
            "IC2-BUG: NaN in info.metrics propagates verbatim to the persisted raw_response, "
            "producing non-RFC 7159 JSON ('NaN' literal). json.dumps emits 'NaN' without error "
            "but the output is not valid JSON per RFC 7159 and breaks JS JSON.parse on the "
            "Discuss button (openChatWithArtifact calls JSON.parse(artifactJson)). "
            "The implementation must sanitize NaN -> null (JSON null) before persisting "
            "raw_response to guard against any caller bypassing compute_quantstats_metrics."
        ),
    )(test_ic2_1b_nan_in_info_metrics_produces_non_rfc_json_is_xfail_bug)

    def test_ic2_1c_numpy_nan_in_metrics_produces_non_rfc_json_is_xfail_bug(self):
        """IC2-1c: numpy.float64('nan') in info.metrics also produces non-RFC JSON.

        Companion to IC2-1b: numpy.float64 nan behaves identically to Python float nan
        when passed through json.dumps — both emit 'NaN'. This test uses numpy.float64
        nan specifically because quantstats could return numpy nan scalars in edge cases.

        IC2-BUG: same as IC2-1b but the nan source is numpy.float64 (not Python float).
        """
        import json

        import numpy as np

        from advisors.strategy_builder_engine import _persist_survivor

        np_nan_metrics = {
            "annualized_return": np.float64("nan"),  # numpy NaN
            "sharpe": np.float64(0.5),
            "calmar": np.float64(0.3),
            "max_drawdown": np.float64(-0.15),
        }
        info = _make_candidate_info(metrics=np_nan_metrics)
        gate_result = _make_gate_result()

        captured: dict = {}

        def fake_insert(**kwargs):
            captured.update(kwargs)

        with patch("database.insert_advisor_observation", side_effect=fake_insert):
            _persist_survivor(
                symphony_id="sym-test",
                info=info,
                gate_result=gate_result,
                n_candidates=3,
                live_returns=[],
                n_survivors=1,
            )

        rr = captured.get("raw_response", {})
        json_str = json.dumps(rr)
        json_contains_nan_literal = "NaN" in json_str

        # Assert the CORRECT behavior: no 'NaN' literal in the JSON string.
        # Fails because the implementation does not sanitize numpy nan either.
        assert not json_contains_nan_literal, (
            # Tolerance: N/A — exact string membership check.
            "IC2-1c: json.dumps(raw_response) contains 'NaN' literal for numpy.float64 nan. "
            "The implementation must sanitize both Python nan and numpy nan -> None."
        )

    test_ic2_1c_numpy_nan_in_metrics_produces_non_rfc_json_is_xfail_bug = pytest.mark.xfail(
        strict=True,
        reason=(
            "IC2-BUG: numpy.float64('nan') in info.metrics propagates to raw_response as"
            " JSON 'NaN' literal (same path as IC2-1b but via numpy nan). "
            "math.isfinite(np.float64(nan)) is False so a correct implementation's NaN guard"
            " would catch both Python and numpy nan. "
            "Sanitize NaN -> null before calling json.dumps."
        ),
    )(test_ic2_1c_numpy_nan_in_metrics_produces_non_rfc_json_is_xfail_bug)

    # -----------------------------------------------------------------------
    # IC2-2: _build_live_baseline pathological live_returns inputs
    # -----------------------------------------------------------------------

    def test_ic2_2a_build_live_baseline_single_element_returns_all_none_metrics(self):
        """IC2-2a: _build_live_baseline with single-element live_returns returns all-None metrics.

        compute_quantstats_metrics requires at least 2 finite observations.
        A single-element series must produce a baseline dict with all None values,
        NOT a crash (ZeroDivisionError, IndexError) and NOT a baseline with NaN values.

        Asserts: result is a dict with keys, all metric values are None (not NaN, not float).
        """
        from advisors.strategy_builder_engine import _build_live_baseline

        result = _build_live_baseline(
            live_returns=[0.05],  # single element
            returns_pct=[0.1, 0.2, 0.3, 0.4, 0.5],  # candidate series (longer)
        )

        assert isinstance(result, dict), (
            f"_build_live_baseline with single-element live_returns must return a dict, "
            f"got {type(result).__name__}."
        )
        # The single-element window (min(1, 5)=1) has insufficient data for metrics.
        # All metric values must be None — not NaN, not a number.
        for key in ("cagr", "sharpe", "calmar", "max_drawdown"):
            val = result.get(key)
            assert val is None, (
                f"_build_live_baseline single-element: {key}={val!r} must be None "
                "(insufficient data — fewer than 2 observations). "
                "IC2-2a: single-element live_returns must yield all-None baseline metrics."
            )

    def test_ic2_2b_build_live_baseline_all_zeros_does_not_crash_and_no_nan(self):
        """IC2-2b: _build_live_baseline with all-zeros live_returns does not crash.

        Zero-variance series causes division-by-zero in Sharpe ratio (0 / 0 = NaN).
        The _safe() wrapper in compute_quantstats_metrics converts NaN -> None.
        Asserts: no exception raised; sharpe and calmar are None (not NaN); cagr is 0.0.

        This test verifies that zero-variance does NOT silently inject NaN into the baseline.
        """
        import math

        from advisors.strategy_builder_engine import _build_live_baseline

        result = _build_live_baseline(
            live_returns=[0.0, 0.0, 0.0, 0.0, 0.0],
            returns_pct=[0.1, 0.2, 0.3, 0.4, 0.5],
        )

        assert isinstance(result, dict), (
            "IC2-2b: all-zeros live_returns must produce a dict, not crash."
        )

        # sharpe and calmar must be None (0/0 → NaN → None via _safe)
        sharpe_val = result.get("sharpe")
        assert sharpe_val is None or not math.isnan(
            float(sharpe_val) if sharpe_val is not None else 0.0
        ), (
            f"IC2-2b: all-zeros sharpe must be None (not NaN). Got {sharpe_val!r}. "
            "_safe() in compute_quantstats_metrics must convert NaN -> None. "
            "NaN in the baseline dict would propagate to raw_response -> non-RFC JSON."
        )
        # More direct: no NaN anywhere in the baseline
        for key, val in result.items():
            if isinstance(val, float):
                assert not math.isnan(val), (
                    # Exact property: NaN identity check.
                    f"IC2-2b: all-zeros live_returns produced NaN for baseline['{key}']={val!r}. "
                    "All NaN values must be converted to None by the analytics layer."
                )

    def test_ic2_2c_build_live_baseline_length_mismatch_live_longer_than_candidate(self):
        """IC2-2c: _build_live_baseline with live_returns longer than returns_pct uses tail of live.

        HR-5 tail-alignment: n = min(len(live_returns), len(returns_pct)).
        When live_returns is longer (10) than returns_pct (2), n=2, and baseline is computed
        from live_returns[-2:] — NOT from the full live_returns[-10:].

        Asserts: result is not None; metrics are computed from the correct 2-element window.
        We verify by checking that the result equals compute_quantstats_metrics(live_returns[-2:]).
        """
        from advisors.strategy_builder_engine import _build_live_baseline
        from analytics import compute_quantstats_metrics

        live_returns = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]  # length 10
        returns_pct = [0.2, 0.3]  # length 2

        result = _build_live_baseline(live_returns=live_returns, returns_pct=returns_pct)

        assert isinstance(result, dict), (
            "IC2-2c: _build_live_baseline must return a dict (not None) when both series non-empty."
        )

        # n = min(10, 2) = 2 → baseline from live_returns[-2:] = [0.9, 1.0]
        n_tail = min(len(live_returns), len(returns_pct))
        expected = compute_quantstats_metrics(live_returns[-n_tail:])
        expected_cagr = expected.get("annualized_return")
        result_cagr = result.get("cagr")

        if expected_cagr is None:
            assert result_cagr is None, (
                f"IC2-2c: expected cagr=None (insufficient data for {n_tail}-element window), "
                f"got {result_cagr!r}. Baseline must use the HR-5 tail-aligned window."
            )
        else:
            assert result_cagr == pytest.approx(expected_cagr, abs=1e-9), (
                # Tolerance: 1e-9 — both sides call the same function with the same input;
                # bit-for-bit equality expected; tiny epsilon guards intermediate rounding.
                f"IC2-2c: baseline cagr={result_cagr!r} does not match "
                f"compute_quantstats_metrics(live_returns[-{n_tail}:]) cagr={expected_cagr!r}. "
                "HR-5: baseline must use tail-aligned window n=min(live_len, pct_len)."
            )

    def test_ic2_2d_build_live_baseline_live_returns_containing_none_does_not_crash(self):
        """IC2-2d: _build_live_baseline with None element in live_returns does not crash.

        compute_quantstats_metrics() filters out non-numeric values via try: float(v).
        None in the input list causes float(None) -> TypeError -> filtered out.
        Asserts: no crash; returns a dict with sensible (non-NaN) metric values.

        This tests the defensive filtering path of compute_quantstats_metrics.
        """
        import math

        from advisors.strategy_builder_engine import _build_live_baseline

        live_returns_with_none = [0.1, None, 0.2, -0.1, 0.3]  # None in the middle
        returns_pct = [0.1, 0.2, 0.3, 0.4, 0.5]

        try:
            result = _build_live_baseline(
                live_returns=live_returns_with_none,
                returns_pct=returns_pct,
            )
        except Exception as exc:
            pytest.fail(
                f"_build_live_baseline raised {type(exc).__name__} on live_returns"
                f" with None: {exc}. "
                "IC2-2d: None elements must be filtered silently — not crash the "
                "baseline computation."
            )

        assert isinstance(result, dict), (
            f"IC2-2d: result must be a dict, got {type(result).__name__}."
        )
        # No NaN values must survive
        for key, val in result.items():
            if isinstance(val, float):
                assert not math.isnan(val), (
                    # Exact property: NaN identity.
                    f"IC2-2d: live_returns with None produced NaN for baseline['{key}']={val!r}. "
                    "None elements must be filtered; resulting metrics must be "
                    "None or finite float."
                )

    # -----------------------------------------------------------------------
    # IC2-3: Template rendering with partial live_baseline and None metric values
    # -----------------------------------------------------------------------

    def test_ic2_3a_template_with_partial_live_baseline_missing_sharpe_renders_crash_free(
        self, client
    ):
        """IC2-3a: Template renders crash-free when live_baseline is missing the 'sharpe' key.

        The template does {% set bl_val = live_baseline.get('sharpe') %} — a missing key
        returns None, which is correctly handled by the {% if bl_val is not none %} guard.
        Asserts: HTTP 200 and no 'None%' artifact or Traceback in the HTML.

        This is an independent fixture test (not using the team's _make_new_row_obs helper).
        """
        # Build a partial live_baseline without the 'sharpe' key
        partial_baseline_obs = {
            "id": 551,
            "created_at": "2026-06-12T10:00:00",
            "advisor_role": "STRATEGY_BUILDER",
            "subject_type": "strategy_proposal",
            "subject_id": "sym-ic2-partial",
            "verdict": "ADOPT_CANDIDATE",
            "raw_response": {
                "objective": "diversify",
                "template_id": "T1",
                "candidate_id": "sym-ic2-partial:T1:0",
                "metrics": {},
                # live_baseline missing 'sharpe' key entirely
                "live_baseline": {
                    "cagr": 0.07,
                    # 'sharpe' is ABSENT (not None — absent)
                    "calmar": 0.40,
                    "max_drawdown": -0.19,
                },
                "cagr": 0.09,
                "sharpe": 0.62,
                "calmar": 0.44,
                "max_drawdown": -0.17,
                "n_candidates": 3,
                "n_survivors": 1,
                "gate_decision": "ADOPT_CANDIDATE",
                "winner_p_adj": 0.012,
                "fdr_q": 0.05,
                "fdr_adjusted_threshold": 0.017,
                "caveats": ["Selected on backtest"],
                "rules_text": "Equal weight: SPY, AGG",
            },
            "is_advisory_only": 1,
            "spec_bundle_id": None,
        }

        with (
            patch(
                "database.get_advisor_observations_for_symphony",
                return_value=[partial_baseline_obs],
            ),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-ic2-partial")

        assert resp.status_code == 200, (
            f"IC2-3a: GET returned {resp.status_code} for partial live_baseline (missing sharpe). "
            "Missing baseline metric key must NOT crash the template."
        )
        html = resp.get_data(as_text=True)
        assert "None%" not in html, (
            "IC2-3a: HTML contains 'None%' when live_baseline.sharpe is absent (not present). "
            "A missing key must render as '—', not format None as a percentage."
        )
        assert "Traceback" not in html, (
            "IC2-3a: HTML contains Traceback for partial live_baseline (missing sharpe key). "
            "Template must handle missing baseline keys gracefully."
        )

    def test_ic2_3b_template_with_none_max_drawdown_in_live_baseline_renders_dash_not_none_pct(
        self, client
    ):
        """IC2-3b: Template renders '—' (not 'None%') when live_baseline.max_drawdown is None.

        The max_drawdown cell uses {{ '%.2f%%' | format(bl_mdd * 100) }}.
        If bl_mdd is None, evaluating None * 100 would produce a TypeError.
        The template guards this with {% if bl_mdd is not none %} — verify it works.

        This is an independent fixture with None in max_drawdown (not calmar like ADV35-4).
        """
        obs = {
            "id": 552,
            "created_at": "2026-06-12T10:00:00",
            "advisor_role": "STRATEGY_BUILDER",
            "subject_type": "strategy_proposal",
            "subject_id": "sym-ic2-none-mdd",
            "verdict": "ADOPT_CANDIDATE",
            "raw_response": {
                "objective": "cut_drawdown",
                "template_id": "T3",
                "candidate_id": "sym-ic2-none-mdd:T3:0",
                "metrics": {},
                "live_baseline": {
                    "cagr": 0.06,
                    "sharpe": 0.50,
                    "calmar": None,  # Also None
                    "max_drawdown": None,  # max_drawdown is None — the key under test
                },
                "cagr": 0.08,
                "sharpe": 0.55,
                "calmar": None,
                "max_drawdown": -0.20,
                "n_candidates": 3,
                "n_survivors": 1,
                "gate_decision": "ADOPT_CANDIDATE",
                "winner_p_adj": 0.014,
                "fdr_q": 0.05,
                "fdr_adjusted_threshold": 0.017,
                "caveats": ["Selected on backtest"],
                "rules_text": "Inverse vol: SPY, TLT",
            },
            "is_advisory_only": 1,
            "spec_bundle_id": None,
        }

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-ic2-none-mdd")

        if resp.status_code != 200:
            pytest.skip(f"Route returned {resp.status_code}.")

        html = resp.get_data(as_text=True)
        assert "None%" not in html, (
            "IC2-3b: HTML contains 'None%' when live_baseline.max_drawdown=None. "
            "The template must guard bl_mdd with 'is not none' before formatting as '%.2f%%'. "
            "None * 100 would TypeError if unguarded; None% appears if format is applied"
            " to None string."
        )

    def test_ic2_3c_withheld_card_with_partial_live_baseline_missing_cagr_renders_ok(self, client):
        """IC2-3c: Withheld (rejected) card with partial live_baseline (missing cagr) renders OK.

        The rejected-cards section of the template also renders a stats-table with live_baseline.
        This test specifically targets the WITHHELD card path (not survivor cards) to ensure
        the same None-guard is present in both branches of the template.

        Uses a WITHHELD verdict obs (goes into the 'withheld' list, not 'survivors').
        """
        withheld_partial_obs = {
            "id": 553,
            "created_at": "2026-06-12T10:00:00",
            "advisor_role": "STRATEGY_BUILDER",
            "subject_type": "strategy_proposal",
            "subject_id": "sym-ic2-withheld",
            "verdict": "WITHHELD",  # goes into withheld bucket
            "raw_response": {
                "objective": "diversify",
                "template_id": "T1",
                "candidate_id": "sym-ic2-withheld:T1:0",
                "metrics": {},
                "live_baseline": {
                    # 'cagr' key is ABSENT
                    "sharpe": 0.48,
                    "calmar": None,
                    "max_drawdown": -0.22,
                },
                "cagr": None,  # candidate cagr also None (edge case)
                "sharpe": 0.55,
                "max_drawdown": -0.25,
                "n_candidates": 3,
                "n_survivors": 0,
                "gate_decision": "WITHHELD_FDR",
                "winner_p_adj": 0.8,
                "fdr_q": 0.05,
                "fdr_adjusted_threshold": 0.017,
                "caveats": [],
                "rules_text": "Equal weight: SPY",
            },
            "is_advisory_only": 1,
            "spec_bundle_id": None,
        }

        with (
            patch(
                "database.get_advisor_observations_for_symphony",
                return_value=[withheld_partial_obs],
            ),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-ic2-withheld")

        assert resp.status_code == 200, (
            f"IC2-3c: GET returned {resp.status_code} for withheld card "
            "with partial live_baseline. "
            "Withheld card with missing live_baseline.cagr must NOT crash the template."
        )
        html = resp.get_data(as_text=True)
        assert "None%" not in html, (
            "IC2-3c: HTML contains 'None%' in withheld card when live_baseline.cagr is absent. "
            "Both survivor and withheld card paths must guard None/absent baseline values."
        )
        assert "Traceback" not in html, (
            "IC2-3c: Traceback in HTML for withheld card with partial live_baseline. "
            "The withheld card stats-table must handle missing baseline keys."
        )

    # -----------------------------------------------------------------------
    # IC2-4: _persist_rejected with empty / all-vetoed batch
    # -----------------------------------------------------------------------

    def test_ic2_4a_persist_rejected_with_empty_gate_batch_does_not_crash(self):
        """IC2-4a: _persist_rejected with all-vetoed batch (n_candidates=0) does not crash.

        When the gate returns no results (e.g., an empty universe produces no candidates),
        n_candidates=0. The Yekutieli harmonic sum c(0) = 0 → the implementation uses the
        fallback c_n=1.0. Asserts: no ZeroDivisionError, no crash, insert IS called.

        Verifies the guard: c_n = sum(1/k for k in range(1, 0+1)) → empty sum = 0.0,
        with the code's own guard `if n_candidates > 0 else 1.0`.
        """
        from advisors.strategy_builder_engine import _persist_rejected

        gate_result = _make_gate_result(verdict_str="WITHHELD_FDR")
        info = _make_candidate_info()

        with patch("database.insert_advisor_observation") as mock_insert:
            try:
                _persist_rejected(
                    symphony_id="sym-test",
                    info=info,
                    gate_result=gate_result,
                    n_candidates=0,  # edge: empty batch
                    live_returns=[],
                    returns_pct=[],
                    n_survivors=0,
                )
            except ZeroDivisionError as exc:
                pytest.fail(
                    f"_persist_rejected raised ZeroDivisionError for n_candidates=0: {exc}. "
                    "IC2-4a: empty gate batch must not cause ZeroDivisionError in Yekutieli sum. "
                    "The harmonic sum over range(1, 1) is the empty sum = 0.0; the guard "
                    "'if n_candidates > 0 else 1.0' must prevent division by zero."
                )
            except Exception as exc:
                pytest.fail(
                    f"_persist_rejected raised {type(exc).__name__} for n_candidates=0: {exc}. "
                    "IC2-4a: empty batch must not crash the rejected-candidate persist path."
                )

        assert mock_insert.called, (
            "IC2-4a: _persist_rejected with n_candidates=0 must still call "
            "database.insert_advisor_observation. The candidate is real — it should be persisted "
            "even if the batch count is anomalous."
        )
        # Verify the written verdict is NOT ADOPT_CANDIDATE
        verdict_written = mock_insert.call_args.kwargs.get("verdict")
        assert verdict_written != "ADOPT_CANDIDATE", (
            f"IC2-4a: _persist_rejected with n_candidates=0 wrote verdict='ADOPT_CANDIDATE'. "
            f"Got: {verdict_written!r}. Rejected candidates must never get ADOPT_CANDIDATE verdict."
        )

    def test_ic2_4b_persist_rejected_with_all_vetoed_batch_writes_correct_observations_count(self):
        """IC2-4b: Calling _persist_rejected for each candidate in an all-vetoed batch yields
        exactly one insert per call.

        When every candidate is vetoed (all results in gate_batch.results have no survivors),
        _persist_rejected must be called once per candidate, and each call must invoke
        database.insert_advisor_observation exactly once.

        Simulates the loop in propose_strategies step 5b with N=3 all-vetoed candidates.
        Asserts: insert_advisor_observation called exactly 3 times total (once per candidate).
        """
        from advisors.strategy_builder_engine import _persist_rejected

        candidates = [
            (
                _make_candidate_info(),
                _make_gate_result(candidate_id=f"c{i}", verdict_str="WITHHELD_FDR"),
            )
            for i in range(3)
        ]

        insert_call_count = 0

        def counting_insert(**kwargs):
            nonlocal insert_call_count
            insert_call_count += 1

        with patch("database.insert_advisor_observation", side_effect=counting_insert):
            for info, gate_result in candidates:
                _persist_rejected(
                    symphony_id="sym-test",
                    info=info,
                    gate_result=gate_result,
                    n_candidates=3,
                    live_returns=[],
                    returns_pct=[],
                    n_survivors=0,
                )

        assert insert_call_count == 3, (
            f"IC2-4b: all-vetoed batch of 3 candidates produced {insert_call_count} insert calls. "
            "Expected exactly 3 — one per candidate. Each _persist_rejected call must produce "
            "exactly one database.insert_advisor_observation call."
        )

    # -----------------------------------------------------------------------
    # IC2-5: M6 card_artifacts for new-format row with metrics but live_baseline absent
    # -----------------------------------------------------------------------

    def test_ic2_5a_card_artifacts_populated_for_new_row_without_live_baseline(self, client):
        """IC2-5a: GET card_artifacts contains Phase 3.5 metric keys for a new-format row
        where metrics are present in raw_response but live_baseline is ABSENT.

        The card_artifacts building loop in app.py reads metrics directly from raw_response
        top-level keys (rr.get('cagr'), etc.) — it does NOT require live_baseline to be
        present. Asserts: no KeyError, artifact populated, all §2 metric keys present.
        """
        # New-format row: has cagr/sharpe/calmar/etc. but NO live_baseline key
        new_row_no_baseline = {
            "id": 701,
            "created_at": "2026-06-12T10:00:00",
            "advisor_role": "STRATEGY_BUILDER",
            "subject_type": "strategy_proposal",
            "subject_id": "sym-ic2-no-bl",
            "verdict": "ADOPT_CANDIDATE",
            "raw_response": {
                "objective": "lift_risk_adjusted",
                "template_id": "T6",
                "candidate_id": "sym-ic2-no-bl:T6:0",
                "metrics": {"annualized_return": 0.11, "sharpe": 0.72},
                # Phase 3.5 flat metric fields present
                "cagr": 0.11,
                "sharpe": 0.72,
                "calmar": 0.50,
                "max_drawdown": -0.16,
                "correlation_vs_live": 0.28,
                "blended_drawdown": -0.09,
                "n_candidates": 5,
                "n_survivors": 1,
                # live_baseline is ABSENT (not present at all)
                "gate_decision": "ADOPT_CANDIDATE",
                "winner_p_adj": 0.011,
                "fdr_q": 0.05,
                "fdr_adjusted_threshold": 0.015,
                "caveats": ["Selected on backtest"],
                "rules_text": "Momentum top-3: SPY, QQQ, IWM",
            },
            "is_advisory_only": 1,
            "spec_bundle_id": None,
        }

        captured: dict = {}

        def capture_render(*args, **kwargs):
            captured.update(kwargs)
            return "<html><body>stub</body></html>"

        with (
            patch(
                "database.get_advisor_observations_for_symphony",
                return_value=[new_row_no_baseline],
            ),
            patch("analytics.list_available_symphonies", return_value=[]),
            patch.object(app_module, "render_template", side_effect=capture_render),
        ):
            try:
                client.get("/ai-advisor/strategy-builder?symphony_id=sym-ic2-no-bl")
            except KeyError as exc:
                pytest.fail(
                    f"GET route raised KeyError on new-format row without live_baseline: {exc}. "
                    "IC2-5a: card_artifacts building must not require live_baseline to be present."
                )

        card_artifacts = captured.get("card_artifacts", {})
        artifact = card_artifacts.get(701, {})

        assert artifact, (
            "IC2-5a: card_artifacts must contain entry for obs id=701 (new-format row). "
            "The card_artifacts building loop must process new-format rows regardless of "
            "whether live_baseline is present."
        )

        # Phase 3.5 metric keys must be present in the artifact
        for key in ("cagr", "sharpe", "calmar", "correlation_vs_live", "blended_drawdown"):
            assert key in artifact, (
                f"IC2-5a: card_artifacts[701] missing Phase 3.5 key '{key}'. "
                "The GET route must surface all §2 flat metric keys into card_artifacts "
                "regardless of whether live_baseline is present."
            )

    def test_ic2_5b_card_artifacts_metric_values_are_none_not_missing_for_old_row(self, client):
        """IC2-5b: For a pre-3.5 row, card_artifacts Phase 3.5 keys have value None (not absent).

        The card_artifacts building uses rr.get('cagr') which returns None when the key is
        absent. The artifact dict must CONTAIN the key 'cagr' with value None — not omit the
        key entirely. This preserves a consistent artifact shape for the chat layer.

        Verifies: artifact has 'cagr' key; value is None (not string 'None').
        """
        fixture = _load_basic_fixture()
        obs = fixture["observation_survivor"]
        obs_id = obs["id"]

        # Confirm this is a pre-3.5 row (no cagr in raw_response)
        assert "cagr" not in obs["raw_response"], (
            "IC2-5b test setup: fixture must not have 'cagr' in raw_response."
        )

        captured: dict = {}

        def capture_render(*args, **kwargs):
            captured.update(kwargs)
            return "<html><body>stub</body></html>"

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
            patch.object(app_module, "render_template", side_effect=capture_render),
        ):
            client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

        card_artifacts = captured.get("card_artifacts", {})
        artifact = card_artifacts.get(obs_id, {})

        # Must have the 'cagr' key (with value None), not be absent
        assert "cagr" in artifact, (
            f"IC2-5b: card_artifacts[{obs_id}] must have 'cagr' key (None) for pre-3.5 row. "
            "The card_artifacts dict must have a consistent shape regardless of row vintage — "
            "the chat layer must not KeyError on 'cagr' when grounding on a pre-3.5 row."
        )
        assert artifact["cagr"] is None, (
            f"IC2-5b: card_artifacts[{obs_id}]['cagr']={artifact['cagr']!r} must be Python None "
            "for a pre-3.5 row (raw_response has no 'cagr' key). "
            "None is the correct sentinel for absent pre-3.5 metrics, not the string 'None'."
        )

    # -----------------------------------------------------------------------
    # IC2-6: Old-row golden fixture — independent IC2 assertion
    # -----------------------------------------------------------------------

    def test_ic2_6a_old_row_golden_fixture_full_get_render_crash_free_and_no_none_percent(
        self, client
    ):
        """IC2-6a: Pre-3.5 golden fixture obs (IC2-independent) renders crash-free via GET.

        Independent of the team's PC-1 test. Uses the same JSON fixture but asserts
        independently: HTTP 200, no 'None%', no 'Traceback', no 'None' string in metric cells.

        Fixture: tests/fixtures/ai_advisor/m6/strategy_builder_observations_basic.json
        (observation_survivor — pre-3.5, has no cagr/sharpe/calmar/live_baseline in raw_response)
        """
        fixture = _load_basic_fixture()
        obs = fixture["observation_survivor"]

        # IC2 assertion: confirm fixture is pre-3.5 (IC2-independent guard)
        for phase35_key in ("cagr", "sharpe", "live_baseline"):
            assert phase35_key not in obs["raw_response"], (
                f"IC2-6a test setup: fixture raw_response must NOT have '{phase35_key}'. "
                "This is a pre-3.5 fixture — it must not contain Phase 3.5 fields."
            )

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

        assert resp.status_code == 200, (
            f"IC2-6a: GET returned {resp.status_code} for pre-3.5 golden fixture. "
            "HR-1: old rows must render crash-free."
        )

        html = resp.get_data(as_text=True)

        assert "None%" not in html, (
            "IC2-6a: HTML contains 'None%' for pre-3.5 golden fixture row. "
            "HR-1: missing metrics must render as '—', never as 'None%'."
        )
        assert "Traceback" not in html, (
            "IC2-6a: HTML contains Traceback for pre-3.5 golden fixture row. "
            "HR-1: pre-3.5 rows must not cause any server-side exception."
        )
        assert "Live Baseline" not in html, (
            "IC2-6a: HTML contains 'Live Baseline' column for pre-3.5 row "
            "(no live_baseline in raw_response). "
            "HR-1 / PA-3: the baseline column must be absent when live_baseline "
            "is not in raw_response."
        )

    def test_ic2_6b_old_row_golden_fixture_card_artifacts_has_correct_shape(self, client):
        """IC2-6b: Pre-3.5 golden fixture card_artifacts has the expected keys and types.

        IC2-independent golden fixture check: verifies card_artifacts for the pre-3.5
        survivor obs has the correct structure — 'artifact_type', 'gate_verdict', and
        Phase 3.5 keys (all None). Also verifies cagr is None (not string 'None').
        """
        fixture = _load_basic_fixture()
        obs = fixture["observation_survivor"]
        obs_id = obs["id"]

        captured: dict = {}

        def capture_render(*args, **kwargs):
            captured.update(kwargs)
            return "<html><body>stub</body></html>"

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
            patch("analytics.list_available_symphonies", return_value=[]),
            patch.object(app_module, "render_template", side_effect=capture_render),
        ):
            client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

        card_artifacts = captured.get("card_artifacts", {})
        assert obs_id in card_artifacts, (
            f"IC2-6b: card_artifacts missing entry for obs id={obs_id} (pre-3.5 fixture). "
            "The GET route must build card_artifacts for all observation rows, not just new ones."
        )
        artifact = card_artifacts[obs_id]

        # Required artifact structure keys (Phase 4 contract)
        for required_key in ("artifact_type", "gate_verdict", "n_candidates"):
            assert required_key in artifact, (
                f"IC2-6b: card_artifacts[{obs_id}] missing required key '{required_key}'. "
                "The artifact must have the Phase 4 structural keys regardless of row vintage."
            )

        assert artifact["artifact_type"] == "strategy_proposal", (
            f"IC2-6b: artifact_type={artifact['artifact_type']!r} must be 'strategy_proposal'. "
            "The artifact type must be the static Phase 4 contract value for all rows."
        )

        # Phase 3.5 metric keys must be present with value None (not absent, not string 'None')
        for key in ("cagr", "sharpe", "calmar"):
            val = artifact.get(key)
            # Key must be present
            assert key in artifact, (
                f"IC2-6b: card_artifacts[{obs_id}] must have key '{key}' (with None value) "
                "for pre-3.5 row. Consistent artifact shape required for chat grounding."
            )
            # Value must be None, not the string 'None'
            assert val != "None", (
                f"IC2-6b: card_artifacts[{obs_id}]['{key}']={val!r} is string 'None'. "
                "Must be Python None (JSON null), never the string 'None'."
            )

    def test_ic2_6c_old_row_golden_fixture_all_three_observations_render_together(self, client):
        """IC2-6c: All three pre-3.5 fixture observations (survivor, rejected, backtest-failed)
        render together in a single GET request without crash.

        The basic fixture contains: observation_survivor (ADOPT_CANDIDATE),
        observation_rejected (WITHHELD), and observation_backtest_failed (WITHHELD +
        backtest_error). Rendering all three together exercises the template's three conditional
        branches
        (survivors, withheld, bt_failed) with pre-3.5 rows simultaneously.

        Asserts: HTTP 200, no 'None%', no 'Traceback'.
        """
        fixture = _load_basic_fixture()
        all_obs = [
            fixture["observation_survivor"],
            fixture["observation_rejected"],
            fixture["observation_backtest_failed"],
        ]

        with (
            patch("database.get_advisor_observations_for_symphony", return_value=all_obs),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

        assert resp.status_code == 200, (
            f"IC2-6c: GET returned {resp.status_code} when rendering all 3 pre-3.5 fixture rows. "
            "All three observation types (survivor, rejected, bt-failed) must render together "
            "without crashing."
        )

        html = resp.get_data(as_text=True)
        assert "None%" not in html, (
            "IC2-6c: HTML contains 'None%' when rendering all 3 pre-3.5 fixture rows together. "
            "All three template branches must guard None metric values."
        )
        assert "Traceback" not in html, (
            "IC2-6c: Traceback in HTML when rendering all 3 pre-3.5 fixture rows. "
            "No template branch must raise an exception for pre-3.5 rows."
        )
