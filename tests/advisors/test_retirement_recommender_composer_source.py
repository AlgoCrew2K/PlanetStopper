"""RED tests -- Retirement Recommender composer source + run summary
(Cycle 2e AC-2, AC-3; feature-plans/retirement-visibility.md).

Module under test: ``advisors.retirement_recommender``.

NEW surface (pinned in .claude/tdd-handoff.md):
  * ``build_recommendations(*, db_file=None, days=RETIREMENT_LOOKBACK_DAYS,
    source="shadow")`` -- ``source`` is ``"shadow"`` (byte-for-byte today's
    behaviour, covered by the untouched pre-existing suite) or ``"composer"``.
  * ``build_recommendations_with_summary(**same kwargs) -> (list[dict], dict)``;
    ``build_recommendations`` returns its first element.
  * ``STRESS_GATE_MIN_ALIGNED_DAYS`` derived from the stress constants.
  * ``COMPOSER_FETCH_MIN_INTERVAL_SECONDS`` (>= 1.0), enforced via
    ``time.sleep`` between consecutive Composer fetches.

The Composer fetch seam is ``advisors.composer_series_client.
fetch_symphony_daily_returns(account_id, symphony_id) -> SeriesResult`` -- the
tests patch it (network is the only thing mocked; the gates, screen, composite
and Pearson math all run for real). ``time.sleep`` is patched with a fake
clock so the >= 1 s spacing is observable without sleeping.

Series are deterministic seeded constructions in PERCENT scale (the unit
``get_symphony_bot_and_held_daily_returns`` / ``compute_quantstats_metrics``
already use). Each scenario below was verified against the pre-change gate
functions to land in exactly the gate outcome its name claims -- see the
scenario builders' docstrings.
"""

from __future__ import annotations

import ast
import inspect
import logging
import math
import pathlib
import random
import re
from datetime import UTC, date, datetime, timedelta

import pytest

import database as db_module
from tests.advisors._retirement_recommender_reference import seed_state_db, trading_days

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_COMPOSER_BASIS_LABEL = "realized daily returns (Composer, deposit-adjusted)"  # AC-2 literal
_SHADOW_BASIS_LABEL = "actual-traded (bot) daily returns"

_REQUIRED_SUMMARY_KEYS = {
    "run_id",
    "run_at",
    "source",
    "basis_label",
    "status",
    "error_type",
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
}
_BLOCKED_KEYS = (
    "blocked_uncertainty",
    "blocked_stress_too_thin",
    "blocked_stress_below_threshold",
    "blocked_no_candidate",
)


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as _rr  # noqa: PLC0415

    return _rr


# ---------------------------------------------------------------------------
# Deterministic series scenarios (percent-scale daily returns)
# ---------------------------------------------------------------------------

_N_MASTER = 300
_MASTER_START = date(2025, 8, 1)
_DATES = [(_MASTER_START + timedelta(days=i)).isoformat() for i in range(_N_MASTER)]


def _tail(values: list[float], n: int) -> dict[str, float]:
    """The last `n` observations, all aligned on the SAME trailing dates so
    series of different lengths overlap on exactly the shorter one's span."""
    return dict(zip(_DATES[-n:], values[-n:], strict=True))


def _gauss(seed: int, n: int, mu: float = 0.05, sd: float = 0.8) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, sd) for _ in range(n)]


def _follower(base, seed, beta=0.9, drift=-0.04, noise=0.1) -> list[float]:
    rng = random.Random(seed)
    return [beta * b + drift + rng.gauss(0, noise) for b in base]


_BASE = _gauss(11, _N_MASTER)  # "X": the strong anchor symphony
_WEAK = _follower(_BASE, 12)  # "W": ~0.99-correlated with X, strictly weaker composite
_INDEP = _gauss(13, _N_MASTER, mu=0.08, sd=0.9)  # "Z": independent of both (|r| ~ 0.02)


def _series_pair_scenario() -> dict[str, dict[str, float]]:
    """X + W over 250 days (r ~ 0.99, stressed r ~ 0.91, W the weaker composite)
    plus an independent Z. Verified: exactly one screened pair (W,X) which
    passes both gates; candidate = W."""
    return {"X": _tail(_BASE, 250), "W": _tail(_WEAK, 250), "Z": _tail(_INDEP, 250)}


def _series_uncertainty_scenario() -> dict[str, dict[str, float]]:
    """X (250d) + F (40d). F tracks X's last 40 days at r ~ 0.78: clears the
    0.65 screen but its Fisher-z CI lower bound (n=40) is < 0.65 -> blocked by
    the uncertainty gate (verified)."""
    rng = random.Random(26)
    f = [b + rng.gauss(0, 0.62) for b in _BASE[-40:]]
    return {"X": _tail(_BASE, 250), "F": _tail(f, 40)}


def _series_thin_stress_scenario() -> dict[str, dict[str, float]]:
    """X (250d) + Y (150d, r ~ 0.99). Aligned n=150 passes the uncertainty gate
    but ceil(0.05 * 150) = 8 < STRESS_MIN_OBS=10 -> stressed correlation is
    None -> blocked_stress_too_thin (verified)."""
    y = _follower(_BASE[-150:], 31, beta=0.95, drift=0.0, noise=0.1)
    return {"X": _tail(_BASE, 250), "Y": _tail(y, 150)}


def _series_stress_below_scenario() -> dict[str, dict[str, float]]:
    """X (250d) + G (250d): identical except on X's 13 worst days, where G's
    values are anti-aligned inside that set. Full r ~ 0.99 (screen + CI pass)
    but stressed r ~ -0.96 -> blocked_stress_below_threshold (verified)."""
    xs = _BASE[-250:]
    worst = sorted(range(250), key=lambda i: xs[i])[:13]
    g = list(xs)
    vals = [xs[i] for i in worst]
    for i, v in zip(worst, reversed(vals), strict=True):
        g[i] = v
    return {"X": _tail(_BASE, 250), "G": _tail(g, 250)}


def _series_no_candidate_scenario() -> dict[str, dict[str, float]]:
    """H + I: strictly positive daily returns, r ~ 0.9998, stressed r ~ 0.97.
    Both gates pass, but an all-positive series has no downside so its Sortino
    and Calmar are None -> both ineligible -> no candidate -> blocked_no_candidate
    (verified)."""
    rng_h = random.Random(41)
    h = [0.05 + 0.10 * rng_h.random() for _ in range(250)]
    rng_i = random.Random(42)
    i_ = [0.9 * v + 0.005 + rng_i.gauss(0, 0.0005) for v in h]
    return {"H": _tail(h, 250), "I": _tail(i_, 250)}


def _series_cluster_scenario() -> dict[str, dict[str, float]]:
    """A, B, C mutually ~0.99-correlated with A > B > C in composite: all three
    pairs pass both gates; candidates are B (from A,B) and C (from A,C and B,C)
    -> 3 produced pairs collapse to 2 recommendations (per-candidate dedup)."""
    b = _follower(_BASE, 51, beta=0.95, drift=-0.02, noise=0.1)
    c = _follower(_BASE, 52, beta=0.9, drift=-0.06, noise=0.1)
    return {"A": _tail(_BASE, 250), "B": _tail(b, 250), "C": _tail(c, 250)}


def _series_combined_scenario() -> dict[str, dict[str, float]]:
    """X, W (250d pair that passes), Y (150d follower), Z (independent).
    4 symphonies -> 6 pairs; screened: (W,X) passes -> 1 rec; (W,Y) and (X,Y)
    -> blocked_stress_too_thin (2); the 3 pairs with Z are not screened."""
    y = _follower(_BASE[-150:], 31, beta=0.95, drift=0.0, noise=0.1)
    return {
        "X": _tail(_BASE, 250),
        "W": _tail(_WEAK, 250),
        "Y": _tail(y, 150),
        "Z": _tail(_INDEP, 250),
    }


# ---------------------------------------------------------------------------
# Environment: isolated DB + roster + patched Composer fetch seam + fake clock
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "retirement_composer_source.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    db_module.run_migrations()
    return db_path


def _seed_roster(accounts: dict[str, str | None], names: dict[str, str] | None = None) -> None:
    """bot_state roster: one entry per symphony; ``account`` omitted when None."""
    roster = {}
    for sid, acct in accounts.items():
        entry = {"name": (names or {}).get(sid, sid), "logic_holdings": {}}
        if acct is not None:
            entry["account"] = acct
        roster[sid] = entry
    db_module.save_state(roster)


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


@pytest.fixture()
def clock(monkeypatch):
    c = _FakeClock()
    monkeypatch.setattr("time.sleep", c.sleep)
    return c


class _FetchSpy:
    """Replaces ``fetch_symphony_daily_returns``; records (clock-now, account,
    symphony) per call and serves a SeriesResult per symphony id."""

    def __init__(self, clock, by_symphony):
        self.clock = clock
        self.by_symphony = by_symphony
        self.calls: list[tuple[float, str, str]] = []

    def __call__(self, account_id, symphony_id):
        from advisors.composer_series_client import SeriesResult  # noqa: PLC0415

        self.calls.append((self.clock.now, account_id, symphony_id))
        item = self.by_symphony[symphony_id]
        if isinstance(item, SeriesResult):
            return item
        return SeriesResult(
            returns=dict(item),
            points=len(item),
            first_date=min(item) if item else None,
            last_date=max(item) if item else None,
            error=None,
        )


@pytest.fixture()
def composer(monkeypatch, isolated_db, clock):
    """Install the roster + fetch spy in one call:
    ``spy = composer(series_by_symphony, accounts=None, names=None)``.

    Default accounts: every symphony -> ``acct-<id>``. Pass ``accounts`` to
    override (``None`` value = no account key)."""

    def _install(series_by_symphony, accounts=None, names=None):
        import advisors.composer_series_client as client  # noqa: PLC0415

        accts = accounts if accounts is not None else {s: f"acct-{s}" for s in series_by_symphony}
        _seed_roster(accts, names)
        spy = _FetchSpy(clock, series_by_symphony)
        monkeypatch.setattr(client, "fetch_symphony_daily_returns", spy)
        return spy

    return _install


def _run(rr, **kwargs):
    return rr.build_recommendations_with_summary(source="composer", **kwargs)


def _assert_reconciles(summary: dict) -> None:
    """pairs_screened = blocked_* total + pairs that produced a recommendation.
    Valid whenever no candidate is flagged against two siblings (no dedup),
    in which case produced pairs == recommendations."""
    blocked = sum(summary[k] for k in _BLOCKED_KEYS)
    assert summary["pairs_screened"] == blocked + summary["recommendations"], summary


# ===========================================================================
# AC-2: composer source
# ===========================================================================


class TestComposerSourceProducesRecommendations:
    def test_composer_series_drive_a_recommendation_with_the_composer_basis_label(
        self, rr, composer
    ):
        composer(_series_pair_scenario())
        recs, summary = _run(rr)

        assert summary["status"] == "ok"
        assert len(recs) == 1
        rec = recs[0]
        assert rec["candidate_id"] == "W", "W is the weaker composite of the (W,X) pair"
        assert rec["sibling_id"] == "X"
        assert rec["basis_label"] == _COMPOSER_BASIS_LABEL
        assert summary["basis_label"] == _COMPOSER_BASIS_LABEL
        assert summary["source"] == "composer"
        assert rec["uncertainty_gate_passed"] is True
        assert rec["structural_redundancy_gate_passed"] is True
        assert rec["n_obs"] == 250

    def test_recommendation_keeps_the_existing_raw_response_schema(self, rr, composer):
        composer(_series_pair_scenario())
        (rec,) = _run(rr)[0]
        expected = {
            "candidate_id",
            "sibling_id",
            "correlation",
            "ci_lower",
            "ci_upper",
            "n_obs",
            "candidate_composite",
            "sibling_composite",
            "candidate_metrics",
            "sibling_metrics",
            "uncertainty_gate_passed",
            "structural_redundancy_gate_passed",
            "stressed_correlation",
            "holdings_overlap",
            "basis_label",
        }
        assert expected <= set(rec)
        assert rec["correlation"] >= rr.CORRELATION_SCREEN_THRESHOLD

    def test_the_shadow_analytics_reader_is_never_called_under_the_composer_source(
        self, rr, composer, monkeypatch
    ):
        import analytics  # noqa: PLC0415

        shadow_calls = []
        monkeypatch.setattr(
            analytics,
            "get_symphony_bot_and_held_daily_returns",
            lambda *a, **kw: shadow_calls.append((a, kw)),
        )
        composer(_series_pair_scenario())
        _run(rr)
        assert shadow_calls == [], "composer source must never read shadow_history"

    def test_fetch_is_called_with_each_symphonys_own_account_in_roster_order(self, rr, composer):
        scenario = _series_pair_scenario()
        spy = composer(scenario, accounts={"X": "acct-alpha", "W": "acct-beta", "Z": "acct-gamma"})
        _run(rr)

        assert [(acct, sym) for _t, acct, sym in spy.calls] == [
            ("acct-beta", "W"),
            ("acct-alpha", "X"),
            ("acct-gamma", "Z"),
        ], "one fetch per live symphony, sorted by symphony id, each with ITS account id"


class TestComposerLookbackWindow:
    def test_only_the_most_recent_lookback_days_are_used_by_default(self, rr, composer):
        # 300 days of the strong pair; default lookback is RETIREMENT_LOOKBACK_DAYS (250).
        composer({"X": _tail(_BASE, 300), "W": _tail(_WEAK, 300), "Z": _tail(_INDEP, 300)})
        recs, summary = _run(rr)

        assert summary["lookback_days"] == rr.RETIREMENT_LOOKBACK_DAYS
        assert summary["series_days_max"] == rr.RETIREMENT_LOOKBACK_DAYS
        assert summary["series_days_min"] == rr.RETIREMENT_LOOKBACK_DAYS
        assert [r["n_obs"] for r in recs] == [rr.RETIREMENT_LOOKBACK_DAYS]

    def test_an_explicit_days_argument_overrides_the_default_lookback(self, rr, composer):
        composer({"X": _tail(_BASE, 300), "W": _tail(_WEAK, 300), "Z": _tail(_INDEP, 300)})
        _recs, summary = _run(rr, days=200)

        assert summary["lookback_days"] == 200
        assert summary["series_days_max"] == 200
        assert summary["series_days_min"] == 200

    def test_a_series_shorter_than_the_lookback_keeps_all_of_it(self, rr, composer):
        composer(_series_thin_stress_scenario())  # X=250d, Y=150d
        _recs, summary = _run(rr)
        assert summary["series_days_min"] == 150
        assert summary["series_days_max"] == 250


class TestComposerSourceExclusions:
    def _three(self):
        s = _series_pair_scenario()
        return s

    def test_a_symphony_without_an_account_id_is_excluded_never_backfilled_from_shadow(
        self, rr, composer, monkeypatch
    ):
        import analytics  # noqa: PLC0415

        shadow_calls = []
        monkeypatch.setattr(
            analytics,
            "get_symphony_bot_and_held_daily_returns",
            lambda *a, **kw: shadow_calls.append((a, kw)),
        )
        scenario = self._three()
        spy = composer(scenario, accounts={"X": "acct-x", "W": "acct-w", "Z": None})
        recs, summary = _run(rr)

        assert shadow_calls == [], (
            "one basis per run: no shadow backfill for the account-less symphony"
        )
        assert "Z" not in [sym for _t, _a, sym in spy.calls], "no account -> nothing to fetch"
        assert summary["roster_size"] == 3
        assert summary["symphonies_with_series"] == 2
        assert summary["symphonies_without_series"] == [
            {"symphony_id": "Z", "reason": "no_account"}
        ]
        assert summary["status"] == "ok"
        assert all("Z" not in (r["candidate_id"], r["sibling_id"]) for r in recs)

    def test_a_failed_fetch_excludes_the_symphony_and_reports_the_client_error(self, rr, composer):
        from advisors.composer_series_client import SeriesResult  # noqa: PLC0415

        scenario = self._three()
        scenario["Z"] = SeriesResult(
            returns={}, points=0, first_date=None, last_date=None, error="HTTP 503"
        )
        recs, summary = _run_with(rr, composer, scenario)

        (excluded,) = summary["symphonies_without_series"]
        assert excluded["symphony_id"] == "Z"
        assert "HTTP 503" in excluded["reason"]
        assert excluded["reason"] != "no_account"
        assert summary["symphonies_with_series"] == 2
        assert summary["status"] == "ok"
        assert len(recs) == 1

    def test_a_series_with_fewer_than_two_returns_is_excluded_as_too_short(self, rr, composer):
        from advisors.composer_series_client import SeriesResult  # noqa: PLC0415

        scenario = self._three()
        scenario["Z"] = SeriesResult(
            returns={_DATES[-1]: 0.5},
            points=1,
            first_date=_DATES[-1],
            last_date=_DATES[-1],
            error=None,
        )
        _recs, summary = _run_with(rr, composer, scenario)

        assert summary["symphonies_without_series"] == [
            {"symphony_id": "Z", "reason": "series_too_short"}
        ]
        assert summary["symphonies_with_series"] == 2

    def test_exactly_two_returns_is_enough_to_keep_a_symphony(self, rr, composer):
        scenario = self._three()
        scenario["Z"] = {_DATES[-2]: 0.1, _DATES[-1]: -0.2}
        _recs, summary = _run_with(rr, composer, scenario)

        assert summary["symphonies_without_series"] == []
        assert summary["symphonies_with_series"] == 3
        assert summary["series_days_min"] == 2


def _run_with(rr, composer, scenario, **kwargs):
    composer(scenario)
    return _run(rr, **kwargs)


class TestComposerFetchSpacing:
    def test_consecutive_composer_fetches_are_at_least_one_second_apart(self, rr, composer, clock):
        scenario = {**_series_pair_scenario(), "Y": _series_thin_stress_scenario()["Y"]}
        spy = composer(scenario)
        _run(rr)

        assert len(spy.calls) == 4
        assert rr.COMPOSER_FETCH_MIN_INTERVAL_SECONDS >= 1.0, "Composer's documented 1 req/s limit"
        times = [t for t, _a, _s in spy.calls]
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        # 0.05 s slack: the fake clock advances only via sleep(); real elapsed time between
        # the (instant) fake fetches is not added to it, so an implementation that sleeps
        # `interval - real_elapsed` lands a few microseconds under the interval.
        assert all(g >= rr.COMPOSER_FETCH_MIN_INTERVAL_SECONDS - 0.05 for g in gaps), gaps


# ===========================================================================
# AC-3: run summary
# ===========================================================================


class TestSummaryEnvelope:
    def test_summary_carries_every_documented_key(self, rr, composer):
        composer(_series_pair_scenario())
        _recs, summary = _run(rr)
        assert set(summary) >= _REQUIRED_SUMMARY_KEYS, _REQUIRED_SUMMARY_KEYS - set(summary)

    def test_run_id_is_a_uuid4_hex_and_unique_per_run(self, rr, composer):
        composer(_series_pair_scenario())
        a = _run(rr)[1]["run_id"]
        b = _run(rr)[1]["run_id"]
        assert re.fullmatch(r"[0-9a-f]{32}", a)
        assert re.fullmatch(r"[0-9a-f]{32}", b)
        assert a != b

    def test_run_at_is_a_timezone_aware_utc_iso_timestamp_for_now(self, rr, composer):
        composer(_series_pair_scenario())
        before = datetime.now(UTC)
        run_at = _run(rr)[1]["run_at"]
        after = datetime.now(UTC)

        parsed = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)
        assert before - timedelta(seconds=2) <= parsed <= after + timedelta(seconds=2)

    def test_recommendations_count_equals_the_returned_list_length(self, rr, composer):
        composer(_series_pair_scenario())
        recs, summary = _run(rr)
        assert summary["recommendations"] == len(recs) == 1

    def test_screen_threshold_is_the_modules_constant(self, rr, composer):
        composer(_series_pair_scenario())
        assert _run(rr)[1]["screen_threshold"] == rr.CORRELATION_SCREEN_THRESHOLD

    def test_error_type_is_none_on_a_clean_run(self, rr, composer):
        composer(_series_pair_scenario())
        assert _run(rr)[1]["error_type"] is None

    def test_build_recommendations_equals_the_first_element_of_the_summary_variant(
        self, rr, composer
    ):
        composer(_series_pair_scenario())
        via_summary = _run(rr)[0]
        via_plain = rr.build_recommendations(source="composer")
        assert via_plain == via_summary
        assert via_plain, "sanity: the scenario produces a recommendation"


class TestPairCountsPerGateOutcome:
    """Each scenario isolates ONE gate outcome; counts are exact by construction."""

    def test_a_passing_pair_with_an_independent_third_symphony(self, rr, composer):
        composer(_series_pair_scenario())
        _recs, s = _run(rr)
        assert s["roster_size"] == 3
        assert s["symphonies_with_series"] == 3
        assert s["pairs_evaluated"] == math.comb(3, 2)
        assert s["pairs_screened"] == 1, "only (W,X) clears the 0.65 screen; Z is independent"
        assert [s[k] for k in _BLOCKED_KEYS] == [0, 0, 0, 0]
        assert s["recommendations"] == 1
        _assert_reconciles(s)

    def test_an_uncorrelated_fleet_screens_nothing_and_is_still_ok(self, rr, composer):
        composer(
            {
                "P": _tail(_gauss(101, 250), 250),
                "Q": _tail(_gauss(102, 250), 250),
                "R": _tail(_gauss(103, 250), 250),
            }
        )
        recs, s = _run(rr)
        assert s["status"] == "ok"
        assert recs == []
        assert s["pairs_evaluated"] == 3
        assert s["pairs_screened"] == 0
        assert [s[k] for k in _BLOCKED_KEYS] == [0, 0, 0, 0]
        assert s["recommendations"] == 0

    def test_a_short_noisy_correlation_is_blocked_by_the_uncertainty_gate(self, rr, composer):
        composer(_series_uncertainty_scenario())
        recs, s = _run(rr)
        assert recs == []
        assert s["pairs_evaluated"] == 1
        assert s["pairs_screened"] == 1
        assert s["blocked_uncertainty"] == 1
        assert [s[k] for k in _BLOCKED_KEYS[1:]] == [0, 0, 0]
        _assert_reconciles(s)

    def test_too_few_aligned_days_for_the_stress_window_counts_as_stress_too_thin(
        self, rr, composer
    ):
        composer(_series_thin_stress_scenario())
        recs, s = _run(rr)
        assert recs == []
        assert s["pairs_screened"] == 1
        assert s["blocked_stress_too_thin"] == 1
        assert s["blocked_uncertainty"] == 0
        assert s["blocked_stress_below_threshold"] == 0
        assert s["blocked_no_candidate"] == 0
        _assert_reconciles(s)

    def test_divergence_on_the_worst_days_counts_as_stress_below_threshold(self, rr, composer):
        composer(_series_stress_below_scenario())
        recs, s = _run(rr)
        assert recs == []
        assert s["pairs_screened"] == 1
        assert s["blocked_stress_below_threshold"] == 1
        assert s["blocked_stress_too_thin"] == 0
        assert s["blocked_uncertainty"] == 0
        assert s["blocked_no_candidate"] == 0
        _assert_reconciles(s)

    def test_a_pair_passing_both_gates_with_no_scoreable_member_counts_as_no_candidate(
        self, rr, composer
    ):
        composer(_series_no_candidate_scenario())
        recs, s = _run(rr)
        assert recs == []
        assert s["pairs_screened"] == 1
        assert s["blocked_no_candidate"] == 1
        assert s["blocked_uncertainty"] == 0
        assert s["blocked_stress_too_thin"] == 0
        assert s["blocked_stress_below_threshold"] == 0
        _assert_reconciles(s)

    def test_a_mixed_fleet_reconciles_across_several_gate_outcomes(self, rr, composer):
        composer(_series_combined_scenario())
        recs, s = _run(rr)

        assert s["roster_size"] == 4
        assert s["pairs_evaluated"] == math.comb(4, 2)
        assert s["pairs_screened"] == 3
        assert s["blocked_stress_too_thin"] == 2, "(W,Y) and (X,Y): Y has only 150 aligned days"
        assert s["recommendations"] == len(recs) == 1
        assert recs[0]["candidate_id"] == "W"
        _assert_reconciles(s)

    def test_pairs_that_collapse_under_per_candidate_dedup_are_counted_per_pair(self, rr, composer):
        composer(_series_cluster_scenario())
        recs, s = _run(rr)

        assert s["pairs_evaluated"] == 3
        assert s["pairs_screened"] == 3, "all three pairs clear the screen and both gates"
        assert [s[k] for k in _BLOCKED_KEYS] == [0, 0, 0, 0]
        assert sorted(r["candidate_id"] for r in recs) == ["B", "C"], (
            "C is the weaker member of two pairs -> one recommendation per candidate"
        )
        assert s["recommendations"] == len(recs) == 2
        # 3 produced pairs, 2 surviving recommendations: reconciliation holds BEFORE dedup.
        blocked = sum(s[k] for k in _BLOCKED_KEYS)
        assert s["pairs_screened"] == blocked + 3


class TestStressGateMinimumDerivedFromConstants:
    def test_stress_gate_min_aligned_days_is_the_smallest_n_that_fills_the_stress_window(
        self, rr, composer
    ):
        composer(_series_pair_scenario())
        summary = _run(rr)[1]

        # Independent derivation: the smallest n with ceil(fraction * n) >= STRESS_MIN_OBS.
        expected = next(
            n
            for n in range(1, 10_000)
            if math.ceil(rr.STRESS_WINDOW_FRACTION * n) >= rr.STRESS_MIN_OBS
        )
        assert summary["stress_gate_min_aligned_days"] == expected
        assert expected == rr.STRESS_GATE_MIN_ALIGNED_DAYS

    def test_the_minimum_is_one_day_short_of_the_first_n_that_works(self, rr):
        n = rr.STRESS_GATE_MIN_ALIGNED_DAYS
        assert math.ceil(rr.STRESS_WINDOW_FRACTION * n) >= rr.STRESS_MIN_OBS
        assert math.ceil(rr.STRESS_WINDOW_FRACTION * (n - 1)) < rr.STRESS_MIN_OBS

    def test_the_module_source_never_hardcodes_the_derived_minimum(self):
        source = (REPO_ROOT / "advisors" / "retirement_recommender.py").read_text(encoding="utf-8")
        literals = {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, int)
        }
        assert 181 not in literals, (
            "stress_gate_min_aligned_days must be derived, not a second literal"
        )


class TestStatusValues:
    def test_a_roster_of_one_is_insufficient_symphonies(self, rr, composer):
        composer({"X": _tail(_BASE, 250)})
        recs, s = _run(rr)
        assert recs == []
        assert s["status"] == "insufficient_symphonies"
        assert s["roster_size"] == 1

    def test_an_empty_roster_is_insufficient_symphonies(self, rr, isolated_db, clock):
        db_module.save_state({})
        recs, s = _run(rr)
        assert recs == []
        assert s["status"] == "insufficient_symphonies"
        assert s["roster_size"] == 0
        assert s["series_days_min"] is None and s["series_days_max"] is None

    def test_every_fetch_failing_is_series_unavailable_with_each_symphony_listed(
        self, rr, composer
    ):
        from advisors.composer_series_client import SeriesResult  # noqa: PLC0415

        fail = SeriesResult(returns={}, points=0, first_date=None, last_date=None, error="HTTP 500")
        composer({"X": fail, "W": fail, "Z": fail})
        recs, s = _run(rr)

        assert recs == []
        assert s["status"] == "series_unavailable"
        assert s["symphonies_with_series"] == 0
        assert sorted(e["symphony_id"] for e in s["symphonies_without_series"]) == ["W", "X", "Z"]
        assert all("HTTP 500" in e["reason"] for e in s["symphonies_without_series"])
        assert s["series_days_min"] is None and s["series_days_max"] is None
        assert s["pairs_evaluated"] == 0
        assert s["error_type"] is None

    def test_only_one_usable_series_among_several_symphonies_is_series_unavailable(
        self, rr, composer
    ):
        from advisors.composer_series_client import SeriesResult  # noqa: PLC0415

        fail = SeriesResult(returns={}, points=0, first_date=None, last_date=None, error="HTTP 500")
        composer({"X": _tail(_BASE, 250), "W": fail})
        _recs, s = _run(rr)
        assert s["status"] == "series_unavailable"
        assert s["roster_size"] == 2
        assert s["symphonies_with_series"] == 1

    def test_a_forced_internal_failure_returns_empty_recs_and_an_internal_error_summary(
        self, rr, composer, monkeypatch, caplog
    ):
        composer(_series_pair_scenario())

        def _boom(*_a, **_k):
            raise RuntimeError("secret-internal-detail-4242")

        monkeypatch.setattr(rr, "screen_correlated_pairs", _boom)
        with caplog.at_level(logging.DEBUG):
            recs, s = _run(rr)

        assert recs == []
        assert s["status"] == "internal_error"
        assert s["error_type"] == "RuntimeError"
        assert s["recommendations"] == 0
        assert set(s) >= _REQUIRED_SUMMARY_KEYS, "an error summary is still a full envelope"
        assert re.fullmatch(r"[0-9a-f]{32}", s["run_id"])
        assert "secret-internal-detail-4242" not in str(s), "D-1: type name only"
        assert "secret-internal-detail-4242" not in caplog.text

    def test_build_recommendations_still_never_raises_on_an_internal_failure(
        self, rr, composer, monkeypatch
    ):
        composer(_series_pair_scenario())

        def _boom(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(rr, "screen_correlated_pairs", _boom)
        assert rr.build_recommendations(source="composer") == []


# ===========================================================================
# Shadow source: unchanged default behaviour + summary
# ===========================================================================


def _shadow_series():
    """(shadow_return, current_return) per day; shadow_return is the bot
    series (element [1]) the shadow source reads."""
    n = 200
    days = trading_days(n)
    a = [-0.10 + 0.20 * i / (n - 1) for i in range(n)]
    b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(a)]
    return {
        "candidate-sym": {d: (a[i], 0.0) for i, d in enumerate(days)},
        "sibling-sym": {d: (b[i], 0.0) for i, d in enumerate(days)},
    }


class TestShadowSourceUnchanged:
    def test_default_source_is_shadow_on_both_entry_points(self, rr):
        for fn in (rr.build_recommendations, rr.build_recommendations_with_summary):
            param = inspect.signature(fn).parameters["source"]
            assert param.default == "shadow", fn.__name__
            assert param.kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__

    def test_shadow_summary_reports_the_shadow_source_and_basis(
        self, rr, tmp_path, monkeypatch, clock
    ):
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=_shadow_series())
        recs, summary = rr.build_recommendations_with_summary(db_file=db_file, days=None)

        assert summary["source"] == "shadow"
        assert summary["basis_label"] == _SHADOW_BASIS_LABEL
        assert summary["status"] == "ok"
        assert set(summary) >= _REQUIRED_SUMMARY_KEYS
        assert summary["roster_size"] == 2
        assert summary["symphonies_with_series"] == 2
        assert summary["series_days_min"] == summary["series_days_max"] == 200
        assert len(recs) == summary["recommendations"] >= 1
        assert all(r["basis_label"] == _SHADOW_BASIS_LABEL for r in recs)

    def test_shadow_recommendations_equal_between_plain_and_summary_entry_points(
        self, rr, tmp_path, monkeypatch, clock
    ):
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=_shadow_series())
        plain = rr.build_recommendations(db_file=db_file, days=None)
        via_summary, _summary = rr.build_recommendations_with_summary(db_file=db_file, days=None)
        assert plain == via_summary
        assert plain

    def test_shadow_source_never_touches_the_composer_client(
        self, rr, tmp_path, monkeypatch, clock
    ):
        import advisors.composer_series_client as client  # noqa: PLC0415

        calls = []
        monkeypatch.setattr(
            client, "fetch_symphony_daily_returns", lambda *a, **k: calls.append((a, k))
        )
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=_shadow_series())
        rr.build_recommendations(db_file=db_file, days=None)
        assert calls == []
        assert clock.sleeps == [], "no Composer fetches -> no rate-limit spacing sleeps"
