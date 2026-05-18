"""
RED tests for port_selector.select_symphony (AC-P2.7.*)
and amendment B2 fixture edge cases.

Fixtures: tests/fixtures/portmode/port_selector/*.json
Tolerance: pytest.approx(rel=1e-6) for score comparisons.
"""

from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "portmode" / "port_selector"

# These imports WILL FAIL until port_selector.py is implemented (RED intent)
from port_selector import select_symphony  # noqa: F401


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


# ---------------------------------------------------------------------------
# AC-P2.7.2: L1 selection metric
# ---------------------------------------------------------------------------

class TestL1SelectionMetric:

    def test_basic_l1_selects_lowest_score_symphony(self):
        """The symphony with the lowest L1 score wins."""
        fx = _load("01_l1_basic_selection.json")
        result = select_symphony(
            target_reduction=fx["inputs"]["target_reduction"],
            candidates=fx["inputs"]["candidates"],
            over_shoot_penalty=fx["inputs"]["over_shoot_penalty"],
        )
        assert result["selected_symphony_id"] == fx["expected"]["selected_symphony_id"]

    def test_basic_l1_scores_are_correct(self):
        """Scores for all candidates must match fixture derivations."""
        fx = _load("01_l1_basic_selection.json")
        result = select_symphony(
            target_reduction=fx["inputs"]["target_reduction"],
            candidates=fx["inputs"]["candidates"],
            over_shoot_penalty=fx["inputs"]["over_shoot_penalty"],
        )
        for sym_id, expected_score in fx["expected"]["scores"].items():
            actual_score = next(
                s["score"] for s in result["all_scores"] if s["symphony_id"] == sym_id
            )
            assert actual_score == pytest.approx(expected_score, rel=1e-6), (
                f"Score mismatch for {sym_id}: expected {expected_score}, got {actual_score}"
            )

    def test_l1_score_handles_tickers_not_in_symphony(self):
        """
        Tickers in target_reduction that are absent from the candidate symphony
        count as full undershoot (exposure_usd=0 for that ticker).
        """
        result = select_symphony(
            target_reduction=[{"ticker": "RARE_TICKER", "amount_usd": 5000.0}],
            candidates=[
                {
                    "symphony_id": "sym-no-rare",
                    "exposure_usd": {"SPY": 10000.0},
                    "total_value": 10000.0,
                    "position_open_date": "2024-01-01",
                }
            ],
            over_shoot_penalty=1.0,
        )
        # Undershoot = full 5000 (can't exit what you don't hold)
        score = next(s["score"] for s in result["all_scores"] if s["symphony_id"] == "sym-no-rare")
        assert score == pytest.approx(5000.0 + 10000.0, rel=1e-6), (
            "RARE_TICKER undershoot=5000 + SPY overshoot=10000 (not in target)"
        )

    def test_l1_score_handles_tickers_not_in_target(self):
        """
        Tickers in candidate symphony that are absent from target_reduction
        count as full overshoot (target_usd=0 for that ticker).
        """
        result = select_symphony(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                {
                    "symphony_id": "sym-extra",
                    "exposure_usd": {"SPY": 5000.0, "EXTRA": 3000.0},
                    "total_value": 8000.0,
                    "position_open_date": "2024-01-01",
                }
            ],
            over_shoot_penalty=1.0,
        )
        # SPY: exact match, score=0. EXTRA: target=0, exposure=3000 -> overshoot=3000*penalty=3000
        score = next(s["score"] for s in result["all_scores"])
        assert score == pytest.approx(3000.0, rel=1e-6)

    def test_over_shoot_penalty_affects_score(self):
        """OVER_SHOOT_PENALTY != 1.0 changes overshoot contribution proportionally."""
        result_penalty_half = select_symphony(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                {
                    "symphony_id": "sym-overshoot",
                    "exposure_usd": {"SPY": 8000.0},
                    "total_value": 8000.0,
                    "position_open_date": "2024-01-01",
                }
            ],
            over_shoot_penalty=0.5,
        )
        # SPY: undershoot=0, overshoot=(8000-5000)*0.5=1500
        score = next(s["score"] for s in result_penalty_half["all_scores"])
        assert score == pytest.approx(1500.0, rel=1e-6)


# ---------------------------------------------------------------------------
# AC-P2.7.3: Euclidean adversarial alternative
# ---------------------------------------------------------------------------

class TestEuclideanAdversarialAlternative:

    def test_euclidean_metric_returns_result_shape(self):
        """Euclidean alternative is implemented and returns the same result shape as L1."""
        from port_selector import select_symphony_euclidean  # noqa: F401
        result = select_symphony_euclidean(
            target_reduction=[{"ticker": "SPY", "amount_usd": 10000.0}],
            candidates=[
                {
                    "symphony_id": "sym-e1",
                    "exposure_usd": {"SPY": 9000.0},
                    "total_value": 9000.0,
                    "position_open_date": "2024-01-01",
                },
                {
                    "symphony_id": "sym-e2",
                    "exposure_usd": {"SPY": 11000.0},
                    "total_value": 11000.0,
                    "position_open_date": "2024-01-02",
                },
            ],
        )
        assert "selected_symphony_id" in result
        assert "all_scores" in result


# ---------------------------------------------------------------------------
# AC-P2.7.4: Tie-breakers
# ---------------------------------------------------------------------------

class TestTieBreakers:

    def test_tie_broken_by_largest_value(self):
        fx = _load("02_tie_breaker_largest_value.json")
        result = select_symphony(
            target_reduction=fx["inputs"]["target_reduction"],
            candidates=fx["inputs"]["candidates"],
            over_shoot_penalty=fx["inputs"]["over_shoot_penalty"],
        )
        assert result["selected_symphony_id"] == fx["expected"]["selected_symphony_id"]
        assert result.get("tie_breaker_used") == "largest_value"

    def test_tie_broken_by_oldest_position_when_equal_value(self):
        """Tie-break step 2: oldest position (longest holding duration)."""
        result = select_symphony(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                {
                    "symphony_id": "sym-newer",
                    "exposure_usd": {"SPY": 5000.0},
                    "total_value": 10000.0,
                    "position_open_date": "2024-06-15",
                },
                {
                    "symphony_id": "sym-older",
                    "exposure_usd": {"SPY": 5000.0},
                    "total_value": 10000.0,
                    "position_open_date": "2024-01-01",
                },
            ],
            over_shoot_penalty=1.0,
        )
        # Both scores=0, same value -> oldest wins (sym-older opened 2024-01-01)
        assert result["selected_symphony_id"] == "sym-older"
        assert result.get("tie_breaker_used") == "oldest_position"

    def test_tie_broken_lexicographically_as_last_resort(self):
        """Tie-break step 3: lexicographic on symphony_id as deterministic last resort."""
        result = select_symphony(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                {
                    "symphony_id": "sym-zzz",
                    "exposure_usd": {"SPY": 5000.0},
                    "total_value": 10000.0,
                    "position_open_date": "2024-03-01",
                },
                {
                    "symphony_id": "sym-aaa",
                    "exposure_usd": {"SPY": 5000.0},
                    "total_value": 10000.0,
                    "position_open_date": "2024-03-01",
                },
            ],
            over_shoot_penalty=1.0,
        )
        # sym-aaa < sym-zzz lexicographically
        assert result["selected_symphony_id"] == "sym-aaa"
        assert result.get("tie_breaker_used") == "lexicographic"


# ---------------------------------------------------------------------------
# AC-P2.7.5: No-good-match abort
# ---------------------------------------------------------------------------

class TestNoGoodMatchAbort:

    def test_abort_when_best_score_exceeds_threshold(self):
        fx = _load("05_no_good_match_abort.json")
        result = select_symphony(
            target_reduction=fx["inputs"]["target_reduction"],
            candidates=fx["inputs"]["candidates"],
            over_shoot_penalty=fx["inputs"]["over_shoot_penalty"],
            min_match_quality_threshold=fx["inputs"]["min_match_quality_threshold"],
        )
        assert result["selected_symphony_id"] is None
        assert result["aborted"] is True
        assert result["abort_reason"] == "best_score_exceeds_threshold"

    def test_no_abort_when_best_score_below_threshold(self):
        """Sanity check: when best score is below threshold, selection proceeds normally."""
        result = select_symphony(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                {
                    "symphony_id": "sym-good",
                    "exposure_usd": {"SPY": 5000.0},
                    "total_value": 5000.0,
                    "position_open_date": "2024-01-01",
                }
            ],
            over_shoot_penalty=1.0,
            min_match_quality_threshold=1000.0,
        )
        assert result["aborted"] is False
        assert result["selected_symphony_id"] == "sym-good"

    def test_triggered_stays_true_on_abort(self):
        """
        AC-P2.7.5: On abort, port_state.triggered stays True — the signal is not cleared.
        The selection step does NOT clear the trigger; that is the caller's responsibility.
        The result dict must carry abort=True to signal the caller NOT to clear triggered.
        """
        fx = _load("05_no_good_match_abort.json")
        result = select_symphony(
            target_reduction=fx["inputs"]["target_reduction"],
            candidates=fx["inputs"]["candidates"],
            over_shoot_penalty=fx["inputs"]["over_shoot_penalty"],
            min_match_quality_threshold=fx["inputs"]["min_match_quality_threshold"],
        )
        assert result["aborted"] is True
        # Caller contract: if aborted=True, triggered stays True
        # The selection function itself must NOT consume or clear the trigger


# ---------------------------------------------------------------------------
# Amendment B2: composition overlap and target-ticker-absent edge cases
# ---------------------------------------------------------------------------

class TestAmendmentB2EdgeCases:

    def test_composition_overlap_scores_independently(self):
        """Amendment B2: same ticker in multiple symphonies — each scored independently."""
        fx = _load("03_amendment_b2_composition_overlap.json")
        result = select_symphony(
            target_reduction=fx["inputs"]["target_reduction"],
            candidates=fx["inputs"]["candidates"],
            over_shoot_penalty=fx["inputs"]["over_shoot_penalty"],
        )
        assert result["selected_symphony_id"] == fx["expected"]["selected_symphony_id"]
        for sym_id, expected_score in fx["expected"]["scores"].items():
            actual = next(s["score"] for s in result["all_scores"] if s["symphony_id"] == sym_id)
            assert actual == pytest.approx(expected_score, rel=1e-6)

    def test_target_ticker_absent_from_all_symphonies(self):
        """Amendment B2: when target names a ticker no symphony holds, undershoot is equal; winner determined by other tickers."""
        fx = _load("04_amendment_b2_target_ticker_absent.json")
        result = select_symphony(
            target_reduction=fx["inputs"]["target_reduction"],
            candidates=fx["inputs"]["candidates"],
            over_shoot_penalty=fx["inputs"]["over_shoot_penalty"],
        )
        assert result["selected_symphony_id"] == fx["expected"]["selected_symphony_id"]

    def test_single_candidate_always_selected_unless_aborted(self):
        """Single candidate: selected trivially unless abort threshold fires."""
        result = select_symphony(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                {
                    "symphony_id": "sym-only",
                    "exposure_usd": {"SPY": 4500.0},
                    "total_value": 4500.0,
                    "position_open_date": "2024-01-01",
                }
            ],
            over_shoot_penalty=1.0,
            min_match_quality_threshold=999999.0,
        )
        assert result["selected_symphony_id"] == "sym-only"
        assert result["aborted"] is False


# ---------------------------------------------------------------------------
# AC-P2.7.7: Telemetry snapshot in result
# ---------------------------------------------------------------------------

class TestSelectionTelemetry:

    def test_result_includes_top_3_scores(self):
        """AC-P2.7.7: result must include scores for all candidate symphonies (top 3)."""
        result = select_symphony(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                {"symphony_id": "s1", "exposure_usd": {"SPY": 5000.0},
                 "total_value": 5000.0, "position_open_date": "2024-01-01"},
                {"symphony_id": "s2", "exposure_usd": {"SPY": 3000.0},
                 "total_value": 3000.0, "position_open_date": "2024-01-02"},
                {"symphony_id": "s3", "exposure_usd": {"SPY": 1000.0},
                 "total_value": 1000.0, "position_open_date": "2024-01-03"},
                {"symphony_id": "s4", "exposure_usd": {"SPY": 4800.0},
                 "total_value": 4800.0, "position_open_date": "2024-01-04"},
            ],
            over_shoot_penalty=1.0,
        )
        assert len(result["all_scores"]) >= 3

    def test_result_includes_target_reduction_payload(self):
        """AC-P2.7.7: result carries the target_reduction payload for gate_state_json."""
        target = [{"ticker": "SPY", "amount_usd": 5000.0}]
        result = select_symphony(
            target_reduction=target,
            candidates=[
                {"symphony_id": "s1", "exposure_usd": {"SPY": 5000.0},
                 "total_value": 5000.0, "position_open_date": "2024-01-01"},
            ],
            over_shoot_penalty=1.0,
        )
        assert result.get("target_reduction") is not None
        assert result["target_reduction"][0]["ticker"] == "SPY"
