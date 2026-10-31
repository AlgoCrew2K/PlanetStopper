"""RED tests -- the retirement run summary stores every evaluated pair and each series' window.

Subject: ``advisors.retirement_recommender.build_recommendations_with_summary(source="composer")``
over the PM's real Composer captures (``tests/advisors/_series_captures.py``). Only the Composer
fetch is replaced: the parser, the pairwise correlations, the screen and the gates all run for real.

The summary gains three keys, additively and with no schema change:

* ``pairs``: one entry per unordered pair of analysed symphonies, the numbers
  ``correlation_diagnostic.compute_pairwise_correlations`` produced for the run's own series
  (``sym_a``, ``sym_b``, ``correlation``, ``n_obs``, ``window_first``, ``window_last``);
* ``series_windows``: per analysed symphony, the window the matrix uses (``window_first``,
  ``window_last``, ``window_days``, from the series after the lookback trim) and the full series
  from ``SeriesResult`` (``series_first``, ``series_last``, ``series_points``);
* ``uncertainty_min_aligned_days``: the uncertainty gate's minimum (``MIN_OBS_FLOOR``).

Expected values are recomputed from the captures or read from the stored real run; none is typed.
"""

from __future__ import annotations

import json

import pytest

import database as db_module
from tests.advisors import _series_captures as captures

_PAIR_KEYS = {"sym_a", "sym_b", "correlation", "n_obs", "window_first", "window_last"}
_WINDOW_KEYS = {
    "window_first",
    "window_last",
    "window_days",
    "series_first",
    "series_last",
    "series_points",
}


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as module  # noqa: PLC0415

    return module


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "retirement_pairs_storage.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    db_module.run_migrations()
    return db_path


def _analysed_series(rr, ids=None, per_symphony_now_et=None) -> dict[str, dict[str, float]]:
    """The series the run itself builds: each capture's returns, last ``lookback`` days, and only
    the symphonies with at least two returns."""
    series = {}
    for sid in ids if ids is not None else captures.capture_ids():
        now_et = (per_symphony_now_et or {}).get(sid, captures.CAPTURE_NOW_ET)
        returns = captures.parsed(sid, now_et).returns
        ordered = sorted(returns.items())[-rr.RETIREMENT_LOOKBACK_DAYS :]
        if len(ordered) >= 2:
            series[sid] = dict(ordered)
    return series


class TestStoredPairs:
    def test_every_evaluated_pair_is_stored_as_the_diagnostic_computed_it(
        self, rr, isolated_db, monkeypatch
    ):
        from advisors import correlation_diagnostic as cd  # noqa: PLC0415

        _recs, summary = captures.run_over_captures(monkeypatch)
        expected = cd.compute_pairwise_correlations(_analysed_series(rr))

        pairs = summary["pairs"]
        assert summary["status"] == "ok"
        assert len(pairs) == len(expected) == summary["pairs_evaluated"]
        assert len(pairs) > 1, "premise: the real run evaluates many pairs"
        for stored, want in zip(pairs, expected, strict=True):
            assert set(stored) == _PAIR_KEYS
            assert (stored["sym_a"], stored["sym_b"]) == (want.sym_a, want.sym_b)
            assert stored["n_obs"] == want.n_obs
            assert stored["correlation"] == want.correlation
            first, last = want.window if want.window else (None, None)
            assert (stored["window_first"], stored["window_last"]) == (first, last)

    def test_there_is_one_pair_per_unordered_pair_with_the_lower_hash_first(
        self, rr, isolated_db, monkeypatch
    ):
        _recs, summary = captures.run_over_captures(monkeypatch)
        keys = [(p["sym_a"], p["sym_b"]) for p in summary["pairs"]]
        assert all(a <= b and a != b for a, b in keys)
        assert len(set(keys)) == len(keys)
        analysed = len(_analysed_series(rr))
        assert len(keys) == analysed * (analysed - 1) // 2

    def test_a_pair_that_never_overlaps_is_stored_undefined_with_zero_days_and_no_window(
        self, rr, isolated_db, monkeypatch
    ):
        """hvPi ends 2026-02-27 (read at 2026-03-01) and q7WQ begins 2026-09-10: no common day."""
        early = captures.CAPTURE_NOW_ET.replace(year=2026, month=3, day=1, hour=12, minute=0)
        read_at = {"hvPiGP1O7AHfutHE3Fjy": early}
        _recs, summary = captures.run_over_captures(monkeypatch, per_symphony_now_et=read_at)

        (stored,) = [
            p
            for p in summary["pairs"]
            if (p["sym_a"], p["sym_b"]) == ("hvPiGP1O7AHfutHE3Fjy", "q7WQHwLzt49cMgFBzHsX")
        ]
        assert stored["n_obs"] == 0
        assert stored["correlation"] is None
        assert stored["window_first"] is None and stored["window_last"] is None

    def test_the_summary_is_strict_json_with_undefined_pairs_as_null(
        self, rr, isolated_db, monkeypatch
    ):
        early = captures.CAPTURE_NOW_ET.replace(month=3, day=1, hour=12)
        _recs, summary = captures.run_over_captures(
            monkeypatch, per_symphony_now_et={"hvPiGP1O7AHfutHE3Fjy": early}
        )
        text = json.dumps(summary, allow_nan=False)
        assert json.loads(text)["pairs"] == summary["pairs"]

    def test_a_run_over_the_captures_reproduces_the_real_stored_runs_counts(
        self, rr, isolated_db, monkeypatch
    ):
        """The real run of 2026-10-22 was computed from these same bodies: relabelling the dates
        and storing the pairs change none of its counts."""
        real = captures.load_captured_summary_row()
        _recs, summary = captures.run_over_captures(monkeypatch)
        for key in (
            "source",
            "basis_label",
            "status",
            "roster_size",
            "symphonies_with_series",
            "symphonies_without_series",
            "series_days_min",
            "series_days_max",
            "lookback_days",
            "pairs_evaluated",
            "pairs_screened",
            "blocked_uncertainty",
            "blocked_stress_too_thin",
            "blocked_stress_below_threshold",
            "blocked_no_candidate",
            "recommendations",
            "screen_threshold",
            "stress_gate_min_aligned_days",
        ):
            assert summary[key] == real[key], key

    def test_the_gate_counts_still_reconcile_with_the_pairs_screened(
        self, rr, isolated_db, monkeypatch
    ):
        _recs, summary = captures.run_over_captures(monkeypatch)
        assert summary["status"] == "ok" and summary["pairs_screened"] > 0, (
            "premise: pairs screened"
        )
        blocked = sum(
            summary[k]
            for k in (
                "blocked_uncertainty",
                "blocked_stress_too_thin",
                "blocked_stress_below_threshold",
                "blocked_no_candidate",
            )
        )
        assert summary["pairs_screened"] == blocked + summary["recommendations"]


class TestStoredSeriesWindows:
    def test_each_analysed_symphony_has_its_matrix_window_and_its_full_series_depth(
        self, rr, isolated_db, monkeypatch
    ):
        _recs, summary = captures.run_over_captures(monkeypatch)
        analysed = _analysed_series(rr)
        windows = summary["series_windows"]

        assert set(windows) == set(analysed)
        for sid, series in analysed.items():
            full = captures.parsed(sid)
            stored = windows[sid]
            assert set(stored) == _WINDOW_KEYS, sid
            assert stored["window_first"] == min(series), sid
            assert stored["window_last"] == max(series), sid
            assert stored["window_days"] == len(series), sid
            assert stored["series_first"] == full.first_date, sid
            assert stored["series_last"] == full.last_date, sid
            assert stored["series_points"] == full.points, sid

    def test_a_series_longer_than_the_lookback_has_a_shorter_window_ending_on_the_same_day(
        self, rr, isolated_db, monkeypatch
    ):
        deepest = "n2ooAZTvBRN6ZzpMmWmU"
        _recs, summary = captures.run_over_captures(monkeypatch)
        stored = summary["series_windows"][deepest]
        assert stored["window_days"] == rr.RETIREMENT_LOOKBACK_DAYS
        assert stored["series_points"] > stored["window_days"], "premise: a deep real series"
        assert stored["window_last"] == stored["series_last"]
        assert stored["window_first"] > stored["series_first"]

    def test_a_series_shorter_than_the_lookback_has_a_window_equal_to_the_whole_series(
        self, rr, isolated_db, monkeypatch
    ):
        shortest = "q7WQHwLzt49cMgFBzHsX"
        _recs, summary = captures.run_over_captures(monkeypatch)
        stored = summary["series_windows"][shortest]
        assert stored["window_days"] == stored["series_points"] < rr.RETIREMENT_LOOKBACK_DAYS
        assert stored["window_first"] == stored["series_first"]
        assert stored["window_last"] == stored["series_last"]

    def test_the_lookback_argument_sets_the_window_and_leaves_the_series_depth_alone(
        self, rr, isolated_db, monkeypatch
    ):
        days = 100
        _recs, summary = captures.run_over_captures(monkeypatch, days=days)
        stored = summary["series_windows"]["n2ooAZTvBRN6ZzpMmWmU"]
        assert stored["window_days"] == days
        assert stored["series_points"] == captures.parsed("n2ooAZTvBRN6ZzpMmWmU").points

    def test_an_excluded_symphony_has_no_window(self, rr, isolated_db, monkeypatch):
        _recs, summary = captures.run_over_captures(monkeypatch)
        excluded = {e["symphony_id"] for e in summary["symphonies_without_series"]}
        assert excluded, "premise: the empty capture is excluded"
        assert excluded.isdisjoint(summary["series_windows"])
        assert all(
            p["sym_a"] not in excluded and p["sym_b"] not in excluded for p in summary["pairs"]
        )


class TestUncertaintyMinimum:
    def test_the_summary_states_the_uncertainty_gates_minimum(self, rr, isolated_db, monkeypatch):
        from advisors import correlation_diagnostic as cd  # noqa: PLC0415

        _recs, summary = captures.run_over_captures(monkeypatch)
        assert summary["uncertainty_min_aligned_days"] == rr.MIN_OBS_FLOOR
        assert summary["uncertainty_min_aligned_days"] == cd.THIN_DATA_THRESHOLD
        assert summary["stress_gate_min_aligned_days"] == rr.STRESS_GATE_MIN_ALIGNED_DAYS
        assert summary["screen_threshold"] == rr.CORRELATION_SCREEN_THRESHOLD

    def test_a_retuned_minimum_is_what_the_next_summary_states(self, rr, isolated_db, monkeypatch):
        """Read at run time: a literal in the summary builder would not follow the constant."""
        retuned = rr.MIN_OBS_FLOOR + 15
        monkeypatch.setattr(rr, "MIN_OBS_FLOOR", retuned)
        _recs, summary = captures.run_over_captures(monkeypatch)
        assert summary["uncertainty_min_aligned_days"] == retuned


class TestEarlyExitSummariesStayCompleteEnvelopes:
    def test_too_few_live_symphonies_still_carries_the_three_keys_empty(
        self, rr, isolated_db, monkeypatch
    ):
        only = captures.capture_ids()[0]
        _recs, summary = captures.run_over_captures(monkeypatch, ids=[only])
        assert summary["status"] == "insufficient_symphonies"
        assert summary["pairs"] == []
        assert summary["series_windows"] == {}
        assert summary["uncertainty_min_aligned_days"] == rr.MIN_OBS_FLOOR

    def test_fewer_than_two_usable_series_keeps_only_the_usable_ones_windows_and_no_pairs(
        self, rr, isolated_db, monkeypatch
    ):
        first, second = "Gpaw3IhZghQPRE6AdEKx", "hvPiGP1O7AHfutHE3Fjy"
        _recs, summary = captures.run_over_captures(
            monkeypatch, ids=[first, second], errors={second: "HTTP 503"}
        )
        assert summary["status"] == "series_unavailable"
        assert summary["pairs"] == []
        assert set(summary["series_windows"]) == {first}
        assert summary["uncertainty_min_aligned_days"] == rr.MIN_OBS_FLOOR


class TestSeedRunFromCaptures:
    def test_the_seeder_writes_a_run_with_pairs_into_the_named_database(
        self, rr, tmp_path, monkeypatch
    ):
        target = str(tmp_path / "seeded_copy.db")
        summary = captures.seed_run_from_captures(target)
        monkeypatch.setenv("DB_PATH", target)
        rows = db_module.get_advisor_observations_for_role(captures.SUMMARY_ROLE, limit=5)
        assert len(rows) == 1
        stored = rows[0]["raw_response"]
        assert stored["run_id"] == summary["run_id"]
        assert stored["pairs"] and stored["series_windows"]
        state = db_module.load_state()
        assert set(state) >= set(summary["series_windows"])
