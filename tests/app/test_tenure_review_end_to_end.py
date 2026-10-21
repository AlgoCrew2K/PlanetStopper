"""The symphony tenure review end to end: the real job, the real runner, the real stores.

Every test here starts a review the way production does (the 03:45 tick with its thread run on
the spot, or the "Run review now" route with its executor run on the spot), lets the real runner
read the Composer records through the URL router (tests/advisors/_composer_http_router.py serves
the captured records under tests/fixtures/composer/tenure-review/; nothing reaches the network and
no sleep is real), and reads the result back through the real
``GET /api/retirement-recommendations`` and the real AI Advisor page. Only the explainer (an LLM
call) is replaced.

World. The captured statistics list six live symphonies; their display names are replaced by
invented ones. Four have a captured series, two do not (an old symphony with an empty answer, and a
young one with an empty answer). The captured series are not correlated enough to make a redundant
pair (their highest correlation is below the 0.65 screen), so the retire-candidate tests add one
derived series: the mid-age symphony's captured dates and start value with every daily return scaled
and a small deterministic wobble added (``jb.twin_series_reply``). Its correlation with its source
is computed here from the two series, never read from the product.

Contract pinned here (the implementer reads this file and the handoff, not the plan):

* ``app._tenure_review_job(trigger, owner=None)`` is the one job both entry points run. With no
  ``owner`` it takes the run slot itself and does nothing when the slot is taken. It reads the clock
  once, as ``datetime.now(_ET)`` through app.py's module-level ``datetime`` name, reads
  ``TENURE_REVIEW_MIN_TRADING_DAYS`` and the three account variables from ``dotenv_values(
  ENV_FILE_PATH)`` with ``os.environ`` as the fallback for a key the file lacks, calls
  ``tenure_review_runner.run_tenure_review(account_ids=, threshold_raw=, now_et=, run_id=,
  trigger=, account_labels=)`` (looked up on the module when called; no ``read_spacing_seconds``),
  and on a completed outcome: stamps every recommendation with ``review_run_id`` (the run's id),
  prepares them through the name, rounding, reuse and explainer steps, writes the recommendation
  rows with ``persist_recommendations``, copies the explanation and its day into the matching
  retire-candidate entries, and writes the run row (role ``TENURE_REVIEW_RUN``, subject type
  ``tenure_review_run``, subject id the run id, verdict ``completed``, the body from
  ``tenure_review.run_record``) LAST, then releases the slot. On an outcome that did not complete,
  or any exception, it writes nothing and records the failure on the run state with a fixed text
  or the exception type name, never ``str(exc)``, and logs the exception type name only.
* An explanation is dated with the US Eastern calendar day the run started (ISO ``YYYY-MM-DD``),
  which is not the as-of day when the run starts before the close; a reused explanation keeps the
  day it was first written; a missing explanation has no day.
"""

from __future__ import annotations

import copy
import logging
import math
from datetime import date, datetime
from unittest.mock import patch

import pytest
import requests

import advisors.retirement_explainer as explainer_module
import advisors.retirement_recommender as recommender_module
import advisors.tenure_review as tr
import app as app_module
import market_calendar
from advisors import composer_portfolio_client as cpc
from advisors import tenure_review_runner as runner_module
from tests.advisors import _composer_http_router as h
from tests.advisors import _retirement_recommender_reference as ref
from tests.advisors import _tenure_review_builders as b
from tests.app import _tenure_review_job_builders as jb
from tests.app import _tenure_review_panel_builders as pb

RUN_ROUTE = "/ai-advisor/retirement/review/run"
APPROVE_ROUTE = "/ai-advisor/retirement/approve"
EXPLANATION = "This symphony repeats a stronger one and adds little of its own."
COMPOSER_BASIS = "realized: Composer account, deposit-adjusted"
LEAK = "sk-ant-LEAKMARK-9f3a71c2d8"
THRESHOLD = 126
EVIDENCE_ITEMS = ("performance", "composite", "redundancy", "backtest", "marginal", "long_vix")
RECOMMENDATION_FIELDS = {
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


# ---------------------------------------------------------------------------
# Fixtures and drivers
# ---------------------------------------------------------------------------


@pytest.fixture()
def explainer_calls(monkeypatch):
    """Replace the LLM explainer; the list collects a copy of every recommendation it saw."""
    calls: list[dict] = []

    def fake(rec):
        calls.append(copy.deepcopy(rec))
        return EXPLANATION

    monkeypatch.setattr(explainer_module, "explain_recommendation", fake)
    return calls


@pytest.fixture()
def world(tmp_path, monkeypatch, explainer_calls):
    """A scratch database, one configured account, a frozen clock (a Tuesday 03:45 ET)."""
    jb.use_scratch_db(monkeypatch, tmp_path)
    env = jb.isolate_env(monkeypatch, tmp_path, accounts=[("ACCOUNT_ROTH", h.ACCOUNT_ID)])
    jb.freeze_app_clock(monkeypatch)
    return env


def run_tick() -> None:
    """The 03:45 tick, with the thread it starts run on the spot."""
    with patch("threading.Thread", jb.ImmediateThread):
        app_module._run_retirement_recommender_tick()


def run_on_demand(client, monkeypatch):
    """The "Run review now" request, with the executor it submits to run the job on the spot."""
    monkeypatch.setattr(
        app_module, "_TENURE_REVIEW_EXECUTOR", jb.ImmediateExecutor(), raising=False
    )
    return client.post(RUN_ROUTE)


def session_days(first: str, last: str) -> list[str]:
    return [d.isoformat() for d in b.nyse_days(date.fromisoformat(first), date.fromisoformat(last))]


def percent_returns(values: dict[str, float], first: str, last: str) -> dict[str, float]:
    """ISO day to the percent change from the previous kept point, inside [first, last]."""
    days = [d for d in sorted(values) if first <= d <= last]
    return {days[i]: (values[days[i]] / values[days[i - 1]] - 1) * 100 for i in range(1, len(days))}


def window_of(item: dict) -> tuple[str, str, int]:
    w = item["window"]
    return w["first"], w["last"], w["days"]


# ---------------------------------------------------------------------------
# The nightly review lists every live symphony
# ---------------------------------------------------------------------------


class TestTheNightlyReviewListsEveryLiveSymphony:
    def test_the_panel_and_the_api_agree_on_every_live_symphony(self, world, client, monkeypatch):
        router = jb.install_world(monkeypatch)
        stats = {e["id"]: e for e in jb.statistics_reply().body["symphonies"]}

        run_tick()

        data = jb.api_after_run(client)
        entries = jb.entries_by_id(data)
        assert sorted(entries) == sorted(stats), "one entry per symphony Composer lists"
        assert len(data["review_entries"]) == len(stats), "no symphony twice"
        run = data["review_run"]
        assert run["trigger"] == "nightly"
        assert run["as_of_trading_day"] == "2026-10-19"
        assert run["threshold_in_effect"] == THRESHOLD
        assert run["threshold_note"] is None
        panel = jb.panel(client)
        for sid, entry in entries.items():
            invested = date.fromisoformat(stats[sid]["invested_since"])
            tenure = len(b.nyse_days(invested, jb.AS_OF))
            assert entry["display_name"] == jb.INVENTED_NAMES[sid]
            assert entry["invested_since"] == stats[sid]["invested_since"]
            assert entry["tenure_trading_days"] == tenure
            assert entry["eligible"] is (tenure >= THRESHOLD)
            node = pb.entry_node(panel, sid)
            assert jb.INVENTED_NAMES[sid] in node.find("tenure-review-name").text()
            assert str(tenure) in node.find("tenure-review-tenure").text()
        assert "2026-10-19" in panel.find("tenure-review-as-of").text()
        assert "2026-10-20 03:45 ET" in panel.find("tenure-review-run-time").text()
        assert str(THRESHOLD) in panel.find("tenure-review-threshold").text()
        # Every Composer request the run made is a read or the backtest: nothing creates,
        # saves, deploys, invests, rebalances or sells.
        assert router.violations == []
        assert {r.kind for r in router.requests} <= set(h.COMPOSER_KINDS)
        assert {r.method for r in router.requests if r.kind != "backtest"} == {"GET"}
        assert {r.method for r in router.requests if r.kind == "backtest"} <= {"POST"}
        # The default pacing of the client is used for every request of the run.
        assert router.sleeps
        assert set(router.sleeps) == {cpc.COMPOSER_READ_SPACING_SECONDS}

    def test_a_symphony_with_a_full_tenure_series_is_scored_over_its_whole_tenure(
        self, world, client, monkeypatch
    ):
        jb.install_world(monkeypatch)

        run_tick()

        entry = jb.entries_by_id(jb.api_after_run(client))[h.MID_ID]
        points = h.series_points(h.MID_ID)
        first = entry["invested_since"]
        kept = [d for d in sorted(points) if first <= d <= "2026-10-19"]
        assert entry["basis"]["kind"] == "composer"
        assert entry["basis"]["label"] == COMPOSER_BASIS
        performance = entry["evidence"]["performance"]
        assert performance["status"] == "available"
        assert window_of(performance) == (kept[0], kept[-1], len(kept)), (
            "the window runs from the first kept point to the as-of day, one day per point"
        )
        oracle_total = (points[kept[-1]] / points[kept[0]] - 1) * 100
        assert performance["figures"]["total_return"]["value"] == pytest.approx(oracle_total)
        assert performance["figures"]["total_return"]["unit"] == "percent"

    def test_the_composite_is_a_score_over_the_latest_threshold_sessions(
        self, world, client, monkeypatch
    ):
        jb.install_world(monkeypatch)

        run_tick()

        entries = jb.entries_by_id(jb.api_after_run(client))
        sessions = b.last_n_nyse_days(jb.AS_OF, THRESHOLD)
        scored = {
            sid: e
            for sid, e in entries.items()
            if e["evidence"]["composite"]["status"] == "available"
        }
        assert set(scored) == {h.OLDEST_ID, h.MID_ID, h.BACKTEST_ID}, (
            "the eligible symphonies with a Composer series on every session of the window"
        )
        for entry in scored.values():
            composite = entry["evidence"]["composite"]
            assert window_of(composite) == (
                sessions[0].isoformat(),
                sessions[-1].isoformat(),
                THRESHOLD,
            )
            figure = composite["figures"]["composite"]
            assert isinstance(figure["value"], float) and math.isfinite(figure["value"])
            assert 0.0 <= figure["value"] <= 1.0
            assert figure["unit"] == "score (0 to 1, fleet-normalized)"
            perf = entry["evidence"]["performance"]["figures"]
            assert perf["max_drawdown"]["value"] <= 0
            assert perf["max_drawdown"]["unit"] == "percent"
            assert math.isfinite(perf["annualized_return"]["value"])
        for sid in (h.YOUNGEST_ID, jb.GPAW_ID, jb.LW4Z_ID):
            assert entries[sid]["evidence"]["composite"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# Tenure below the threshold
# ---------------------------------------------------------------------------


def _below_threshold_world(monkeypatch, days: int):
    since = b.last_n_nyse_days(jb.AS_OF, days)[0].isoformat()
    router = jb.install_world(monkeypatch, twin=True, invested_since={jb.TWIN_ID: since})
    return router, since


class TestASymphonyBelowTheThresholdIsListedWithoutAVerdict:
    @pytest.mark.parametrize("trigger", ["nightly", "on_demand"])
    @pytest.mark.parametrize(
        "days", [THRESHOLD - 1, THRESHOLD - 26], ids=["one day short", "26 short"]
    )
    def test_it_reads_not_yet_eligible_with_the_days_remaining_and_offers_no_decision(
        self, world, client, monkeypatch, trigger, days
    ):
        _below_threshold_world(monkeypatch, days)

        if trigger == "nightly":
            run_tick()
        else:
            assert run_on_demand(client, monkeypatch).status_code == 202

        data = jb.api_after_run(client)
        entry = jb.entries_by_id(data)[jb.TWIN_ID]
        remaining = THRESHOLD - days
        assert data["review_run"]["trigger"] == trigger
        assert entry["tenure_trading_days"] == days
        assert entry["eligible"] is False
        assert entry["days_remaining"] == remaining
        assert entry["eligibility_text"] == (
            f"not yet eligible: {remaining} trading day{'' if remaining == 1 else 's'} remaining"
        )
        assert entry["verdict"] is None
        node = pb.entry_node(jb.panel(client), jb.TWIN_ID)
        assert node.find("retirement-approve-btn") is None
        assert node.find("retirement-reject-btn") is None
        assert entry["symphony_id"] not in {r["candidate_id"] for r in data["recommendations"]}

    def test_it_still_appears_as_a_sibling_in_the_redundancy_evidence_of_a_correlated_peer(
        self, world, client, monkeypatch
    ):
        _below_threshold_world(monkeypatch, THRESHOLD - 1)

        run_tick()

        data = jb.api_after_run(client)
        peer = jb.entries_by_id(data)[h.MID_ID]
        pair = peer["evidence"]["redundancy"]["pair"]
        assert pair is not None and pair["sibling_id"] == jb.TWIN_ID, (
            "the ineligible symphony is still a sibling the peer is compared with"
        )
        assert data["recommendations"] == [], (
            "a pair with an ineligible member yields no retirement candidate"
        )
        assert peer["verdict"] != "retire_candidate"


# ---------------------------------------------------------------------------
# The redundancy pair end to end
# ---------------------------------------------------------------------------


def _twin_pair_oracle():
    """Correlation, interval and observation count of the (mid-age, derived) pair, computed
    from the two series themselves with the repository's independent reference."""
    mid = h.series_points(h.MID_ID)
    twin = jb.twin_series_values()
    first, last = min(mid), "2026-10-19"
    a = percent_returns(mid, first, last)
    c = percent_returns(twin, first, last)
    days = sorted(set(a) & set(c))
    xs, ys = [a[d] for d in days], [c[d] for d in days]
    rho = b.pearson(xs, ys)
    return {
        "n": len(days),
        "rho": rho,
        "ci_lower": ref.reference_fisher_ci_lower(rho, len(days)),
        "ci_upper": ref.reference_fisher_ci_upper(rho, len(days)),
        "stressed": b.oracle_stressed_correlation(xs, ys),
    }


class TestAPassingPairBecomesARetirementCandidate:
    def test_the_gate_values_of_the_pair_are_the_ones_the_series_imply(
        self, world, client, monkeypatch
    ):
        jb.install_world(monkeypatch, twin=True)
        oracle = _twin_pair_oracle()
        assert oracle["rho"] >= 0.65 and oracle["n"] >= b.days_needed_for_structural_gate(), (
            "premise: the derived pair passes both gates"
        )

        run_tick()

        entry = jb.entries_by_id(jb.api_after_run(client))[jb.TWIN_ID]
        redundancy = entry["evidence"]["redundancy"]
        pair = redundancy["pair"]
        assert redundancy["retire_candidate"] is True
        assert pair["sibling_id"] == h.MID_ID
        assert pair["n_obs"] == oracle["n"]
        assert pair["correlation"]["value"] == pytest.approx(oracle["rho"], abs=1e-9)
        assert pair["ci_lower"]["value"] == pytest.approx(oracle["ci_lower"], abs=1e-9)
        assert pair["ci_upper"]["value"] == pytest.approx(oracle["ci_upper"], abs=1e-9)
        assert pair["stressed_correlation"]["value"] == pytest.approx(oracle["stressed"], abs=1e-9)
        assert pair["uncertainty_gate"]["state"] == "passed"
        assert pair["structural_gate"]["state"] == "passed"
        assert entry["verdict"] == "retire_candidate"

    def test_the_candidate_is_listed_with_its_explanation_status_and_decision_buttons(
        self, world, client, monkeypatch, explainer_calls
    ):
        jb.install_world(monkeypatch, twin=True)
        jb.seed_live_names()

        run_tick()

        data = jb.api_after_run(client)
        run_id = data["review_run"]["run_id"]
        entry = jb.entries_by_id(data)[jb.TWIN_ID]
        assert entry["explanation"] == EXPLANATION
        assert entry["explanation_written_on"] == jb.WRITTEN_ON, (
            "dated with the Eastern day the run started, not the as-of day"
        )
        assert entry["approval_status"] is None, "no decision exists yet"
        recs = data["recommendations"]
        assert [(r["candidate_id"], r["sibling_id"]) for r in recs] == [(jb.TWIN_ID, h.MID_ID)]
        assert set(recs[0]) >= RECOMMENDATION_FIELDS, "the existing fields are all still there"
        assert recs[0]["review_run_id"] == run_id
        assert recs[0]["explanation"] == EXPLANATION
        assert recs[0]["approval_status"] == "pending"
        assert [c["candidate_id"] for c in explainer_calls] == [jb.TWIN_ID]
        assert explainer_calls[0]["candidate_name"] == jb.TWIN_NAME
        assert explainer_calls[0]["sibling_name"] == jb.INVENTED_NAMES[h.MID_ID]
        assert recs[0]["candidate_name"] == jb.TWIN_NAME
        node = pb.entry_node(jb.panel(client), jb.TWIN_ID)
        assert "retire" in node.find("tenure-review-verdict-chip").text().lower()
        shown = node.find("retirement-rec-explanation").text()
        assert EXPLANATION in shown and f"written {jb.WRITTEN_ON}" in shown
        assert node.find("retirement-rec-status").text().lower() == "pending"
        assert node.find("retirement-approve-btn") is not None
        assert node.find("retirement-reject-btn") is not None

    def test_a_failing_explainer_leaves_the_candidate_listed_without_an_explanation(
        self, world, client, monkeypatch, caplog
    ):
        jb.install_world(monkeypatch, twin=True)

        def boom(rec):
            raise RuntimeError(f"upstream said {LEAK}")

        monkeypatch.setattr(explainer_module, "explain_recommendation", boom)

        with caplog.at_level(logging.DEBUG):
            run_tick()

        data = jb.api_after_run(client)
        assert data["review_run"] is not None, "an explainer failure never fails the run"
        entry = jb.entries_by_id(data)[jb.TWIN_ID]
        assert entry["verdict"] == "retire_candidate"
        assert entry["explanation"] is None
        assert entry.get("explanation_written_on") is None
        assert data["recommendations"][0]["explanation"] is None
        node = pb.entry_node(jb.panel(client), jb.TWIN_ID)
        assert "explanation unavailable" in node.find("retirement-rec-explanation").text()
        assert node.find("retirement-approve-btn") is not None
        assert LEAK not in caplog.text

    def test_a_second_run_on_the_same_day_reuses_the_stored_explanation(
        self, world, client, monkeypatch, explainer_calls
    ):
        """The recommendations the real runner produces carry the window and run fields the
        comparison must look through; a second run over the same records only differs by its
        run id and its time."""
        jb.install_world(monkeypatch, twin=True)

        run_tick()
        jb.freeze_app_clock(monkeypatch, datetime(2026, 10, 20, 15, 0, tzinfo=jb.ET))
        run_tick()

        assert len(explainer_calls) == 1, "the unchanged pair was explained twice"
        runs = jb.run_rows()
        assert len(runs) == 2
        recs = jb.rec_rows()
        assert len(recs) == 2
        assert {r["raw_response"]["review_run_id"] for r in recs} == {
            r["raw_response"]["run_id"] for r in runs
        }, "each run has its own recommendation row"
        assert {r["raw_response"]["explanation"] for r in recs} == {EXPLANATION}


# ---------------------------------------------------------------------------
# The marginal and long-volatility evidence
# ---------------------------------------------------------------------------


class TestTheComparisonPortfolioIsTheEligibleSymphoniesOnly:
    def test_a_young_symphony_is_listed_as_excluded_and_is_in_neither_leg(
        self, world, client, monkeypatch
    ):
        jb.install_world(monkeypatch)

        run_tick()

        entries = jb.entries_by_id(jb.api_after_run(client))
        eligible = [h.OLDEST_ID, h.MID_ID, h.BACKTEST_ID]
        points = {sid: h.series_points(sid) for sid in eligible}
        starts = {
            sid: min(d for d in points[sid] if d >= entries[sid]["invested_since"])
            for sid in eligible
        }
        first = max(starts.values())
        days = [
            d
            for d in sorted(points[h.OLDEST_ID])
            if first <= d <= "2026-10-19" and all(d in points[x] for x in eligible)
        ]
        marginal = entries[h.OLDEST_ID]["evidence"]["marginal"]
        assert marginal["status"] == "available"
        assert window_of(marginal) == (days[0], days[-1], len(days)), (
            "the window starts at the later eligible invested-since date, set by the youngest "
            "ELIGIBLE symphony, never by the young one"
        )
        excluded = {x["display_name"]: x["reason"] for x in marginal["excluded"]}
        assert excluded[jb.INVENTED_NAMES[h.YOUNGEST_ID]] == "excluded: not yet eligible"
        total = {d: sum(points[s][d] for s in eligible) for d in days}
        without = {d: total[d] - points[h.OLDEST_ID][d] for d in days}
        figures = marginal["figures"]
        assert figures["with_total_return"]["value"] == pytest.approx(
            (total[days[-1]] / total[days[0]] - 1) * 100
        )
        assert figures["without_total_return"]["value"] == pytest.approx(
            (without[days[-1]] / without[days[0]] - 1) * 100
        )


def _long_vix_oracle():
    """Day to the set of live symphonies holding a long-volatility product that day, over the
    NYSE sessions of the captured holdings record."""
    held_by_day = h.holdings_days(h.holdings_recent_body())
    live = set(h.statistics_ids())
    long_vix = {"VIXY", "VIXM", "UVXY", "UVIX", "VXX"}
    return {
        day: {
            sid
            for sid, tickers in by_symphony.items()
            if sid in live and any(t in long_vix and v > 0 for t, v in tickers.items())
        }
        for day, by_symphony in held_by_day.items()
        if market_calendar.is_trading_day(date.fromisoformat(day))
    }


class TestLongVolatilityCoFiring:
    def test_held_and_co_held_days_follow_the_captured_holdings(self, world, client, monkeypatch):
        jb.install_world(monkeypatch)
        holders = _long_vix_oracle()
        assert any(len(h_) >= 2 for h_ in holders.values()), (
            "premise: the capture has a day on which two symphonies hold a long-volatility product"
        )

        run_tick()

        entries = jb.entries_by_id(jb.api_after_run(client))
        checked = 0
        for sid, entry in entries.items():
            item = entry["evidence"]["long_vix"]
            if item["status"] != "available":
                continue
            held = [d for d, who in holders.items() if sid in who]
            co_held = [d for d in held if holders[d] - {sid}]
            figures = item["figures"]
            assert figures["held_days"]["value"] == len(held), sid
            assert figures["co_held_days"]["value"] == len(co_held), sid
            assert figures["held_days"]["unit"] == "days"
            for name in ("peak_share_with", "peak_share_without"):
                share = figures[name]
                assert share["unit"] == "percent"
                assert share["value"] is None or 0.0 <= share["value"] <= 100.0
            checked += 1
        assert checked >= 2, "the item must be available for the symphonies that were evaluated"


# ---------------------------------------------------------------------------
# The backtest window lines up with the realized window
# ---------------------------------------------------------------------------


class TestTheDatedBacktestCoversTheRealizedWindow:
    def test_the_requested_dates_the_returned_dates_and_the_series_window_are_one_window(
        self, world, client, monkeypatch
    ):
        # The captured backtest was requested from 2026-07-20 to 2026-10-19 for this
        # symphony; its statistics date is moved to match, and the threshold to fit the
        # shorter tenure (derived forms of the capture, the only fields changed).
        jb.rewrite_env(world, accounts=[("ACCOUNT_ROTH", h.ACCOUNT_ID)], threshold="60")
        router = jb.install_world(monkeypatch, invested_since={h.BACKTEST_ID: "2026-07-20"})

        response = run_on_demand(client, monkeypatch)

        assert response.status_code == 202
        entry = jb.entries_by_id(jb.api_after_run(client))[h.BACKTEST_ID]
        points = h.series_points(h.BACKTEST_ID)
        kept = [d for d in sorted(points) if "2026-07-20" <= d <= "2026-10-19"]
        performance = entry["evidence"]["performance"]
        backtest = entry["evidence"]["backtest"]
        assert window_of(performance) == (kept[0], kept[-1], len(kept))
        requests_made = router.of_kind("backtest")
        assert len(requests_made) == 1
        body = requests_made[0].body_json()
        assert (body["start_date"], body["end_date"]) == (kept[0], kept[-1]), (
            "the backtest is requested over exactly the realized window"
        )
        assert backtest["status"] == "available", backtest.get("reason")
        assert window_of(backtest)[:2] == (kept[0], kept[-1])
        figures = backtest["figures"]
        realized = figures["realized_total_return"]["value"]
        counterfactual = figures["backtest_total_return"]["value"]
        assert figures["total_return_gap"]["value"] == pytest.approx(realized - counterfactual)
        assert figures["total_return_gap"]["unit"] == "percentage points"
        assert realized == pytest.approx((points[kept[-1]] / points[kept[0]] - 1) * 100), (
            "the realized leg is the same figure the performance item shows"
        )
        data = jb.api_after_run(client)
        assert data["review_run"]["threshold_in_effect"] == 60
        assert data["review_run"]["trigger"] == "on_demand"


# ---------------------------------------------------------------------------
# A run that fails keeps the previous review and writes nothing
# ---------------------------------------------------------------------------

STATISTICS_FAILURES = {
    "401": lambda: h.error_reply(401),
    "500": lambda: h.error_reply(500),
    "429": lambda: h.error_reply(429),
    "timeout": lambda: h.raising(requests.Timeout("read timed out")),
    "non-JSON 200": h.non_json_200,
}
FAILURE_REASONS = {"non-JSON 200": "Composer statistics unreadable"}
SECOND_RUN_AT = datetime(2026, 10, 21, 3, 45, tzinfo=jb.ET)


def _first_good_run(client, monkeypatch, *, twin=True):
    router = jb.install_world(monkeypatch, twin=twin)
    run_tick()
    return router, jb.api_after_run(client)


def _assert_previous_review_kept(client, before: dict, ids_before, reason: str, at: datetime):
    assert jb.new_rows_since(ids_before) == [], "a run that fails writes nothing"
    after = jb.api_after_run(client)
    assert after["review_run"] == before["review_run"], "the previous completed run stays shown"
    assert after["review_entries"] == before["review_entries"]
    assert after["recommendations"] == before["recommendations"]
    failed = after["review_status"]["last_failed_attempt"]
    assert failed["reason"] == reason
    assert datetime.fromisoformat(failed["at"]) == at
    assert after["review_status"]["running"] is False
    assert jb.taken_then_released(), "a failed run must give the run slot back"
    panel = jb.panel(client)
    assert (
        f"latest review attempt failed: {reason}"
        in panel.find("tenure-review-failed-notice").text()
    )
    assert "2026-10-21" in panel.find("tenure-review-failed-notice").text()
    assert len(panel.find_all("tenure-review-entry")) == len(before["review_entries"])
    assert "2026-10-20 03:45 ET" in panel.find("tenure-review-run-time").text()


class TestAFailedStatisticsReadKeepsThePreviousReview:
    @pytest.mark.parametrize("failure", sorted(STATISTICS_FAILURES))
    def test_the_run_does_not_complete_and_the_panel_says_so(
        self, world, client, monkeypatch, failure
    ):
        router, before = _first_good_run(client, monkeypatch)
        ids_before = jb.all_row_ids()
        router.statistics_default = STATISTICS_FAILURES[failure]()
        request_count = len(router.requests)
        jb.freeze_app_clock(monkeypatch, SECOND_RUN_AT)

        run_tick()

        reason = FAILURE_REASONS.get(failure, "Composer statistics unavailable")
        _assert_previous_review_kept(client, before, ids_before, reason, SECOND_RUN_AT)
        new = router.requests[request_count:]
        assert [r.kind for r in new] == ["statistics"], (
            "nothing but the one statistics request is made once it has failed"
        )

    def test_one_account_failing_fails_the_run_even_when_another_was_read(
        self, tmp_path, monkeypatch, client, explainer_calls
    ):
        jb.use_scratch_db(monkeypatch, tmp_path)
        env = jb.isolate_env(
            monkeypatch,
            tmp_path,
            accounts=[("ACCOUNT_INDIVIDUAL", h.ACCOUNT_ID), ("ACCOUNT_ROTH", h.SECOND_ACCOUNT_ID)],
        )
        assert env.exists()
        jb.freeze_app_clock(monkeypatch)
        router = jb.install_world(monkeypatch, twin=True)
        # The first good run: the second account holds no symphonies (the captured record with
        # its array emptied, an edge of the capture).
        router.statistics_by_account[h.SECOND_ACCOUNT_ID] = h.Reply(body={"symphonies": []})
        run_tick()
        before = jb.api_after_run(client)
        ids_before = jb.all_row_ids()
        router.statistics_by_account[h.SECOND_ACCOUNT_ID] = h.error_reply(503)
        request_count = len(router.requests)
        jb.freeze_app_clock(monkeypatch, SECOND_RUN_AT)

        run_tick()

        _assert_previous_review_kept(
            client, before, ids_before, "Composer statistics unavailable", SECOND_RUN_AT
        )
        new = router.requests[request_count:]
        assert [r.kind for r in new] == ["statistics", "statistics"]
        assert [r.match["account"] for r in new] == [h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]

    def test_with_no_account_configured_the_run_does_not_complete(self, world, client, monkeypatch):
        _, before = _first_good_run(client, monkeypatch)
        ids_before = jb.all_row_ids()
        jb.rewrite_env(world, accounts=[])
        jb.freeze_app_clock(monkeypatch, SECOND_RUN_AT)

        run_tick()

        _assert_previous_review_kept(
            client, before, ids_before, "no Composer account configured", SECOND_RUN_AT
        )


# ---------------------------------------------------------------------------
# A rate limit part-way through a run
# ---------------------------------------------------------------------------


class TestARateLimitPartWayThroughTheRun:
    def test_the_run_completes_with_honest_items_asks_for_nothing_more_and_frees_the_slot(
        self, world, client, monkeypatch
    ):
        router = jb.install_world(monkeypatch)
        router.set_series(h.MID_ID, h.error_reply(429))

        run_tick()

        kinds = [r.kind for r in router.requests]
        assert kinds == ["statistics", "series"], (
            f"nothing may be requested after the first 429, got {kinds}"
        )
        data = jb.api_after_run(client)
        assert data["review_run"] is not None, "a rate limit ends the evidence, not the run"
        assert len(data["review_entries"]) == len(jb.stored_and_listed_ids())
        reasons = " ".join(
            (e["basis"].get("fallback_reason") or "") for e in data["review_entries"]
        )
        assert "Composer rate limit" in reasons
        assert all(e["verdict"] != "retire_candidate" for e in data["review_entries"])
        status = data["review_status"]
        assert status["running"] is False
        assert status["last_failed_attempt"] is None, "a completed run is not a failed attempt"
        assert jb.taken_then_released()
        assert len(jb.run_rows()) == 1

    def test_a_second_run_after_it_starts_afresh(self, world, client, monkeypatch):
        router = jb.install_world(monkeypatch)
        router.set_series(h.MID_ID, h.error_reply(429))
        run_tick()
        router.set_series(h.MID_ID, h.reply_from_record(h.series_record_name(h.MID_ID)))
        count = len(router.requests)
        jb.freeze_app_clock(monkeypatch, SECOND_RUN_AT)

        run_tick()

        assert len(router.requests) > count + 2, "the second run read the Composer again"
        newest = jb.api_after_run(client)
        assert len(jb.run_rows()) == 2
        assert jb.entries_by_id(newest)[h.MID_ID]["basis"]["kind"] == "composer"


# ---------------------------------------------------------------------------
# What a run writes, and in what order
# ---------------------------------------------------------------------------


class TestWhatACompletedRunWrites:
    def test_every_row_is_advisory_only_and_the_run_row_comes_last(
        self, world, client, monkeypatch
    ):
        jb.install_world(monkeypatch, twin=True)
        before = jb.all_row_ids()

        run_tick()

        new = jb.new_rows_since(before)
        roles = {r["advisor_role"] for r in new}
        assert roles == {jb.ROLE_RUN, jb.ROLE_REC}, "only the run row and the recommendation rows"
        assert all(r["is_advisory_only"] == 1 for r in new), "every row is advisory-only"
        run_row = max(r["id"] for r in new if r["advisor_role"] == jb.ROLE_RUN)
        rec_ids = [r["id"] for r in new if r["advisor_role"] == jb.ROLE_REC]
        assert rec_ids and max(rec_ids) < run_row, "the recommendation rows are written first"
        run = jb.api_after_run(client)["review_run"]
        stored = jb.run_rows()[0]
        assert stored["subject_id"] == run["run_id"]
        assert stored["verdict"] == "completed"
        assert stored["subject_type"] == "tenure_review_run"
        for row in jb.rec_rows():
            assert row["raw_response"]["review_run_id"] == run["run_id"]

    def test_a_roster_of_zero_symphonies_is_a_completed_empty_run(self, world, client, monkeypatch):
        router = jb.install_world(monkeypatch)
        router.statistics_default = h.Reply(body={"symphonies": []})

        run_tick()

        data = jb.api_after_run(client)
        assert data["review_run"] is not None and data["review_entries"] == []
        assert "No live symphonies" in jb.panel(client).find("tenure-review-no-symphonies").text()


class TestARunThatFailsAfterTheRecommendationsWereWritten:
    def _orphaned_run(self, client, monkeypatch, caplog):
        router, before = _first_good_run(client, monkeypatch)
        first_rec_count = len(jb.rec_rows())
        assert first_rec_count == 1
        seen = {}
        real_runner = runner_module.run_tenure_review

        def spy(**kwargs):
            seen["run_id"] = kwargs["run_id"]
            return real_runner(**kwargs)

        monkeypatch.setattr(runner_module, "run_tenure_review", spy)
        real_persist = recommender_module.persist_recommendations

        def persist_then_fail(recs, **kw):
            real_persist(recs[:1], **kw)
            raise RuntimeError(f"disk said {LEAK}")

        monkeypatch.setattr(recommender_module, "persist_recommendations", persist_then_fail)
        # Same as-of day as the first run, so the pair is still flagged and has a row to write.
        jb.freeze_app_clock(monkeypatch, datetime(2026, 10, 20, 15, 0, tzinfo=jb.ET))
        ids_before = jb.all_row_ids()
        with caplog.at_level(logging.DEBUG):
            run_tick()
        return before, seen, ids_before

    def test_no_run_row_is_written_and_the_orphaned_row_is_never_shown(
        self, world, client, monkeypatch, caplog
    ):
        before, seen, ids_before = self._orphaned_run(client, monkeypatch, caplog)

        new = jb.new_rows_since(ids_before)
        assert [r["advisor_role"] for r in new] == [jb.ROLE_REC], (
            "the one recommendation written before the failure, and no run row"
        )
        orphan = jb.rec_rows()[0]["raw_response"]
        assert orphan["review_run_id"] == seen["run_id"] != before["review_run"]["run_id"]
        after = jb.api_after_run(client)
        assert after["review_run"] == before["review_run"]
        assert after["recommendations"] == before["recommendations"], (
            "the row of a run that never completed is not listed"
        )
        assert all(
            r["review_run_id"] == before["review_run"]["run_id"] for r in after["recommendations"]
        )
        panel = jb.panel(client)
        assert panel.find("tenure-review-failed-notice") is not None
        assert LEAK not in panel.everything()

    def test_the_failure_text_reaches_neither_the_panel_nor_the_api_nor_the_log(
        self, world, client, monkeypatch, caplog
    ):
        self._orphaned_run(client, monkeypatch, caplog)

        data = jb.api_after_run(client)
        failed = data["review_status"]["last_failed_attempt"]
        assert isinstance(failed["reason"], str) and failed["reason"].strip()
        assert LEAK not in str(data)
        assert LEAK not in jb.panel(client).everything()
        assert LEAK not in caplog.text, "the log names the exception type only"
        assert "RuntimeError" in caplog.text or "RuntimeError" in failed["reason"]
        assert all(r.exc_info is None for r in caplog.records if r.name.startswith("app")), (
            "a traceback would carry the exception message"
        )
        assert jb.taken_then_released()


# ---------------------------------------------------------------------------
# A decision made while a review runs
# ---------------------------------------------------------------------------


class TestADecisionMadeWhileAReviewRuns:
    def test_approve_during_a_run_is_recorded_and_shown_on_the_previous_and_the_new_review(
        self, world, client, monkeypatch
    ):
        jb.install_world(monkeypatch, twin=True)
        run_tick()
        first = jb.api_after_run(client)["review_run"]["run_id"]
        real_runner = runner_module.run_tenure_review
        blocked = jb.BlockingRunner(delegate=real_runner).install(monkeypatch)
        executor = jb.ThreadedExecutor()
        monkeypatch.setattr(app_module, "_TENURE_REVIEW_EXECUTOR", executor, raising=False)
        jb.freeze_app_clock(monkeypatch, SECOND_RUN_AT)
        try:
            assert client.post(RUN_ROUTE).status_code == 202
            assert blocked.entered.wait(30), "the second run never started"

            approve = client.post(APPROVE_ROUTE, json={"candidate_id": jb.TWIN_ID})

            assert approve.status_code == 200
            assert approve.get_json()["success"] is True
            assert jb.the_run_state().snapshot()["running"] is True, "approving never ends a run"
            mid_run = jb.panel(client)
            node = pb.entry_node(mid_run, jb.TWIN_ID)
            assert node.find("retirement-rec-status").text().lower() == "approved"
            assert mid_run.find("tenure-review-running-notice") is not None
            assert jb.api_after_run(client)["review_run"]["run_id"] == first, (
                "the previous run is shown while the new one runs"
            )
        finally:
            blocked.release.set()
            executor.wait_all()
            executor.shutdown()

        after = jb.api_after_run(client)
        assert after["review_run"]["run_id"] != first
        entry = jb.entries_by_id(after)[jb.TWIN_ID]
        assert entry["approval_status"] == "approved"
        assert pb.decisions_by_id()[jb.TWIN_ID]["approval_status"] == "approved"


# ---------------------------------------------------------------------------
# Settings are read at each run, and the runner is called the way the job promises
# ---------------------------------------------------------------------------


class TestWhatTheJobHandsTheRunner:
    def test_the_threshold_and_accounts_come_from_the_environment_file_at_each_run(
        self, tmp_path, monkeypatch
    ):
        jb.use_scratch_db(monkeypatch, tmp_path)
        env = jb.isolate_env(
            monkeypatch,
            tmp_path,
            accounts=[("ACCOUNT_INDIVIDUAL", "acct-one"), ("ACCOUNT_TRAD", "acct-three")],
            threshold="100",
            os_env={"ACCOUNT_ROTH": "acct-two", "ACCOUNT_TRAD": "acct-from-process"},
        )
        jb.freeze_app_clock(monkeypatch)
        calls = jb.patch_runner(monkeypatch, [], isolate=False)

        run_tick()
        jb.rewrite_env(env, accounts=[("ACCOUNT_ROTH", "acct-two")], threshold="abc")
        run_tick()
        jb.rewrite_env(env, accounts=[("ACCOUNT_ROTH", "acct-two")])
        run_tick()

        assert [c["threshold_raw"] for c in calls] == ["100", "abc", None], (
            "the threshold is read again at every run"
        )
        assert calls[0]["account_ids"] == ["acct-one", "acct-two", "acct-three"], (
            "the engine's account order; the file wins over the process for a key it holds, and "
            "the process fills in a key the file lacks"
        )
        assert calls[0]["account_labels"] == {
            "acct-one": "Individual",
            "acct-two": "Roth IRA",
            "acct-three": "Trad. IRA",
        }
        assert calls[1]["account_ids"] == ["acct-two", "acct-from-process"]
        runs = [r["raw_response"] for r in reversed(jb.run_rows())]
        assert [r["threshold_in_effect"] for r in runs] == [100, 126, 126]
        assert runs[1]["threshold_note"] == "configured threshold invalid; 126 in use"
        assert runs[2]["threshold_note"] is None

    def test_the_clock_is_read_in_eastern_time_and_no_spacing_override_is_passed(
        self, tmp_path, monkeypatch
    ):
        jb.use_scratch_db(monkeypatch, tmp_path)
        jb.isolate_env(monkeypatch, tmp_path, accounts=[("ACCOUNT_ROTH", "acct-two")])
        jb.freeze_app_clock(monkeypatch, datetime(2026, 10, 20, 3, 45, tzinfo=jb.ET))
        calls = jb.patch_runner(monkeypatch, [], isolate=False)

        run_tick()

        (call,) = calls
        assert call["now_et"] == jb.NOW_ET
        assert call["now_et"].utcoffset() == jb.NOW_ET.utcoffset()
        assert call["trigger"] == "nightly"
        assert isinstance(call["run_id"], str) and call["run_id"]
        assert "read_spacing_seconds" not in call, "the client's own spacing is used"
        assert jb.run_rows()[0]["raw_response"]["run_id"] == call["run_id"]
        assert jb.run_rows()[0]["raw_response"]["started_at"].startswith("2026-10-20T03:45")
