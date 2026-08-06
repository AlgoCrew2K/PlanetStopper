"""RED — BL-8 (audit #118 T3, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
"silent-never-tuned" operator signal.

THE GAP: fallback_oos_alpha == default_oos_alpha byte-exact in 40/40 observed
autotune_runs rows, 0/40 "Adopted AI", oos_alpha = -inf in 40/40 rows —
Planet Stopper has run on 100% stock-default risk parameters for its entire
visible history. The gate is correctly implemented (an honest statistical
null, per the audit's own T3 finding), but the operator has NO signal
distinguishing "tuning tried this week and reverted" from "tuning has never
once engaged."

THE FIX: a new PURE computation, ``analytics.compute_never_adopted_streak``
(name/shape committed here as the test-writer's contract per the plan's
"implementer's discretion on exact SURFACE" — the COMPUTATION contract is
pinned by these tests; see .claude/tdd-handoff.md for the exact seam and the
render-surface follow-up), derived from existing ``autotune_runs`` rows with
NO new schema — computes a "N consecutive runs at default/fallback params"
streak per symphony from ``baseline_decision != "Adopted AI"`` runs, ordered
by ``run_timestamp``.

AC-10: the signal is DISTINCT from the existing per-week baseline_decision
string already shown per run — it communicates the ACCUMULATED pattern
across runs, never merely the latest single-run outcome.

AC-11: honest degrade — fewer than 2 autotune_runs rows (insufficient
history to establish a streak) renders an informative "insufficient
history" state, never a fabricated streak count.

Edge case (plan): a BRAND NEW symphony with a single autotune_runs row must
hit AC-11's insufficient-history degrade — never report "N weeks never
adopted" for N=1 in a way that reads as an alarming multi-week pattern.

Render-surface note (loose pin, PM-approved): the exact operator-facing
surface (AI Advisor Overview panel vs. reporting.py's EOD digest) is left to
the implementer per the plan's own Decisions table ("BL-8's exact display
surface left to implementer"). This file pins the computation + degrade
contract only; a surface-reaching regression test is added in the
sufficiency-review pass once the implementer's chosen seam is known (Toxic
Pair Red/Green/Revise — not omitted, deferred to the correct cycle phase).
"""

from __future__ import annotations

import random

import pytest

import analytics

_ADOPTED = "Adopted AI"
_FALLBACK = "Reverted to Fallback"
_DEFAULT = "Reset to Global Default"


def _row(run_timestamp: str, baseline_decision: str) -> dict:
    return {"run_timestamp": run_timestamp, "baseline_decision": baseline_decision}


class TestStreakComputationBasics:
    def test_all_non_adopted_rows_produce_a_streak_equal_to_row_count(self):
        rows = [
            _row("2026-07-01T04:00:00", _FALLBACK),
            _row("2026-07-08T04:00:00", _DEFAULT),
            _row("2026-07-15T04:00:00", _FALLBACK),
        ]
        result = analytics.compute_never_adopted_streak(rows)

        assert result["status"] == "streak"
        assert result["streak_weeks"] == 3, (
            f"3 consecutive non-Adopted-AI runs must produce streak_weeks=3; "
            f"got {result['streak_weeks']}"
        )

    def test_adopted_ai_row_resets_the_streak_to_only_the_more_recent_runs(self):
        """5 rows, oldest-to-newest: [non-adopted, non-adopted, ADOPTED,
        non-adopted, non-adopted] — the streak counts ONLY the 2 most-recent
        non-adopted runs (walking backward from "now" until hitting the
        Adopted AI row), not all 4 non-adopted rows across the whole
        history."""
        rows = [
            _row("2026-06-01T04:00:00", _FALLBACK),
            _row("2026-06-08T04:00:00", _DEFAULT),
            _row("2026-06-15T04:00:00", _ADOPTED),
            _row("2026-06-22T04:00:00", _FALLBACK),
            _row("2026-06-29T04:00:00", _DEFAULT),
        ]
        result = analytics.compute_never_adopted_streak(rows)

        assert result["status"] == "streak"
        assert result["streak_weeks"] == 2, (
            f"the streak must reset at the Adopted AI row and count only the "
            f"2 non-adopted runs AFTER it; got {result['streak_weeks']}"
        )

    def test_most_recent_run_is_adopted_ai_yields_zero_streak(self):
        rows = [
            _row("2026-06-01T04:00:00", _FALLBACK),
            _row("2026-06-08T04:00:00", _FALLBACK),
            _row("2026-06-15T04:00:00", _ADOPTED),
        ]
        result = analytics.compute_never_adopted_streak(rows)

        assert result["status"] == "streak"
        assert result["streak_weeks"] == 0, (
            f"the most recent run adopted AI — the current streak must be 0, "
            f"not counting older pre-adoption non-adopted runs; got "
            f"{result['streak_weeks']}"
        )

    def test_never_adopted_across_full_visible_history_counts_all_rows(self):
        """The T3 finding's real-world shape: 0/40 Adopted AI across the
        symphony's ENTIRE visible history — the streak equals the full row
        count, with no Adopted AI row anywhere to reset it."""
        rows = [_row(f"2026-{m:02d}-01T04:00:00", _FALLBACK) for m in range(1, 6)]
        result = analytics.compute_never_adopted_streak(rows)

        assert result["status"] == "streak"
        assert result["streak_weeks"] == 5

    def test_order_independent_input_sorted_internally_by_run_timestamp(self):
        """The caller must not be required to pre-sort rows — the function
        sorts by run_timestamp internally, so a scrambled-order input
        produces the SAME result as the chronologically sorted input."""
        chronological = [
            _row("2026-06-01T04:00:00", _FALLBACK),
            _row("2026-06-08T04:00:00", _ADOPTED),
            _row("2026-06-15T04:00:00", _FALLBACK),
            _row("2026-06-22T04:00:00", _DEFAULT),
        ]
        scrambled = list(chronological)
        random.Random(42).shuffle(scrambled)
        # fixture integrity: the shuffle must actually have changed the order
        assert scrambled != chronological

        expected = analytics.compute_never_adopted_streak(chronological)
        actual = analytics.compute_never_adopted_streak(scrambled)

        assert actual == expected, (
            f"scrambled-order input must produce the identical result to the "
            f"chronologically sorted input; got {actual} vs {expected}"
        )


class TestInsufficientHistoryHonestDegrade:
    def test_zero_rows_degrades_honestly(self):
        result = analytics.compute_never_adopted_streak([])
        assert result["status"] == "insufficient_history"
        assert result["streak_weeks"] is None, (
            "insufficient_history must never carry a fabricated streak count"
        )

    def test_single_row_brand_new_symphony_degrades_honestly(self):
        """Edge case (plan): a brand-new symphony with exactly ONE
        autotune_runs row must NOT report a streak — even though that single
        row is technically non-Adopted-AI, reporting streak_weeks=1 would
        read as an alarming multi-week pattern for a symphony that has only
        run once."""
        rows = [_row("2026-07-29T04:00:00", _FALLBACK)]
        result = analytics.compute_never_adopted_streak(rows)

        assert result["status"] == "insufficient_history", (
            f"a single-row symphony must degrade to insufficient_history, not "
            f"report a 1-week streak; got status={result['status']!r}"
        )
        assert result["streak_weeks"] is None


class TestSignalIsDistinctFromLatestRunString:
    def test_result_shape_is_not_a_bare_echo_of_latest_baseline_decision(self):
        """AC-10: the signal must communicate the ACCUMULATED pattern, not
        merely repeat the latest single run's baseline_decision string —
        structural proof: the result dict's own key is never named
        'baseline_decision' (that field already exists per-run; this is a
        NEW derived field), and for a genuine multi-week streak the numeric
        streak_weeks value is NOT itself one of the 3 baseline_decision enum
        strings."""
        rows = [
            _row("2026-06-01T04:00:00", _FALLBACK),
            _row("2026-06-08T04:00:00", _DEFAULT),
            _row("2026-06-15T04:00:00", _FALLBACK),
        ]
        result = analytics.compute_never_adopted_streak(rows)

        assert "baseline_decision" not in result, (
            "the streak result must not re-expose a bare 'baseline_decision' "
            "key — that would just mirror the latest single-run string, not "
            "the accumulated pattern AC-10 requires"
        )
        assert isinstance(result["streak_weeks"], int), (
            f"streak_weeks must be a genuine derived integer count, not one "
            f"of the baseline_decision enum strings; got {result['streak_weeks']!r}"
        )
