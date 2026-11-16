"""Every advisor surface that backtests sends the calibrated slippage default.

Each test runs one entry point of the census (a route through the Flask test client, a
scheduled job through its tick or weekly function) against ``ComposerRouter``, the one
replacement of ``requests``, and reads the body of every backtest request the run sent. Nothing
patches ``run_backtest`` or the client's constant: the body is what the real client built, so a
surface that bypassed the default (an explicit value, or a patched engine binding) would show.

The expected value is the plan's literal 0.001 (``CALIBRATED_SLIPPAGE``), never read from the
client module. Every test first requires that its run sent the requests the surface is known
to send, so a world that sends nothing cannot pass.

The request sequences of the ref are held in
tests/fixtures/backtest_slippage_calibration/request_sequences.json (provenance beside it): they
carry every body field except the slippage, so they pass before and after the change and fail on
any other change to what a surface asks Composer.
"""

from __future__ import annotations

import copy
import json

import pytest

from tests import _asset_swap_llm_support as asl
from tests.advisors import _composer_http_router as h
from tests.advisors import _slippage_surface_worlds as worlds
from tests.advisors import logic_change_http_support as lcs
from tests.advisors._slippage_surface_worlds import CALIBRATED_SLIPPAGE


@pytest.fixture(autouse=True)
def _no_unserved_tenure_requests():
    """The tenure worlds install the live-call guard; its record is checked at teardown. The
    guard's lists are module state, so they are emptied first: a record left by another file's
    test is not this file's."""
    worlds.jb._REFUSED.clear()
    worlds.jb._ROUTERS.clear()
    yield
    worlds.jb.assert_no_unserved_requests()


def _assert_every_body_is_calibrated(run: worlds.SurfaceRun, *, entry: str) -> None:
    assert run.router.violations == [], (
        f"{entry} made a request the Composer router does not serve: "
        f"{[(r.method, r.url) for r in run.router.violations]}"
    )
    sent = worlds.sent_slippages(run.router)
    for index, value in enumerate(sent):
        assert value == CALIBRATED_SLIPPAGE, (
            f"{entry}: backtest request {index} of {len(sent)} carried slippage_percent "
            f"{value!r}, expected {CALIBRATED_SLIPPAGE}"
        )


def _assert_surface(
    entry: str,
    run: worlds.SurfaceRun,
    *,
    min_requests: int,
    expect_spy: bool,
) -> None:
    assert len(run.backtests) >= min_requests, (
        f"{entry} sent {len(run.backtests)} backtest requests; the surface is known to send at "
        f"least {min_requests}, so the world did not drive it"
    )
    if expect_spy:
        spies = worlds.spy_requests(run.router)
        assert spies, f"{entry} sent no SPY baseline request"


def _requests_for_tree(run: worlds.SurfaceRun, tree: dict) -> list:
    return [r for r in run.backtests if r.body_json()["symphony"]["raw_value"] == tree]


def _non_spy(run: worlds.SurfaceRun) -> list:
    spies = {id(r) for r in worlds.spy_requests(run.router)}
    return [r for r in run.backtests if id(r) not in spies]


# ---------------------------------------------------------------------------
# One test per entry point of the census
# ---------------------------------------------------------------------------


def test_strategy_builder_route_sends_the_calibrated_slippage_on_its_spy_and_candidate_bodies(
    monkeypatch, tmp_path
):
    run = worlds.run_strategy_builder_route(monkeypatch, tmp_path)

    # Two compiler checks, the SPY baseline and two candidates.
    _assert_surface("strategy_builder_route", run, min_requests=5, expect_spy=True)
    assert run.outcome["status"] == 200
    assert run.outcome["route"]["error"] is None
    assert len(_non_spy(run)) >= 4, "the compiler and candidate requests are in the census too"
    _assert_every_body_is_calibrated(run, entry="strategy_builder_route")


def test_strategy_builder_weekly_run_sends_the_calibrated_slippage_on_every_body(
    monkeypatch, tmp_path
):
    run = worlds.run_strategy_builder_weekly(monkeypatch, tmp_path)

    _assert_surface("strategy_builder_weekly", run, min_requests=5, expect_spy=True)
    _assert_every_body_is_calibrated(run, entry="strategy_builder_weekly")


def test_asset_swaps_route_sends_the_calibrated_slippage_on_spy_baseline_and_variant_bodies(
    monkeypatch, tmp_path
):
    run = worlds.run_asset_swaps_route(monkeypatch, tmp_path)

    _assert_surface("asset_swaps_route", run, min_requests=3, expect_spy=True)
    assert run.outcome["status"] == 200
    assert _requests_for_tree(run, asl.held_tree()), "the baseline of the held tree was sent"
    _assert_every_body_is_calibrated(run, entry="asset_swaps_route")


def test_asset_swaps_weekly_run_sends_the_calibrated_slippage_on_the_baseline_returns_body(
    monkeypatch, tmp_path
):
    run = worlds.run_asset_swaps_weekly(monkeypatch, tmp_path)

    _assert_surface("asset_swaps_weekly", run, min_requests=4, expect_spy=True)
    baselines = _requests_for_tree(run, asl.held_tree())
    assert len(baselines) >= 2, (
        "the held tree is backtested once for the variant's baseline and once for the "
        "baseline returns of the batch gate, so both bodies are in the census"
    )
    _assert_every_body_is_calibrated(run, entry="asset_swaps_weekly")


def test_logic_changes_route_sends_the_calibrated_slippage_on_spy_baseline_and_variant_bodies(
    monkeypatch, tmp_path
):
    run = worlds.run_logic_changes_route(monkeypatch, tmp_path)

    _assert_surface("logic_changes_route", run, min_requests=3, expect_spy=True)
    assert run.outcome["status"] == 200
    assert _requests_for_tree(run, lcs.load_tree()), "the baseline of the live tree was sent"
    _assert_every_body_is_calibrated(run, entry="logic_changes_route")


def test_logic_changes_weekly_run_sends_the_calibrated_slippage_on_the_baseline_returns_body(
    monkeypatch, tmp_path
):
    run = worlds.run_logic_changes_weekly(monkeypatch, tmp_path)

    _assert_surface("logic_changes_weekly", run, min_requests=4, expect_spy=True)
    baselines = _requests_for_tree(run, lcs.load_tree())
    assert len(baselines) >= 2, (
        "the live tree is backtested once for the variant's baseline and once for the "
        "baseline returns of the batch gate, so both bodies are in the census"
    )
    _assert_every_body_is_calibrated(run, entry="logic_changes_weekly")


def test_frontrunner_route_sends_the_calibrated_slippage_on_incumbent_and_candidate_bodies(
    monkeypatch, tmp_path
):
    run = worlds.run_frontrunner_route(monkeypatch, tmp_path)

    _assert_surface("frontrunner_route", run, min_requests=2, expect_spy=False)
    assert run.outcome["status"] == 202
    assert worlds.spy_requests(run.router) == [], "the Frontrunner Builder runs no SPY baseline"
    _assert_every_body_is_calibrated(run, entry="frontrunner_route")


def test_frontrunner_weekly_run_sends_the_calibrated_slippage_on_incumbent_and_candidate_bodies(
    monkeypatch, tmp_path
):
    run = worlds.run_frontrunner_weekly(monkeypatch, tmp_path)

    _assert_surface("frontrunner_weekly", run, min_requests=2, expect_spy=False)
    assert worlds.spy_requests(run.router) == [], "the Frontrunner Builder runs no SPY baseline"
    _assert_every_body_is_calibrated(run, entry="frontrunner_weekly")


def test_incubation_tick_sends_the_calibrated_slippage_on_the_spy_and_candidate_bodies(
    monkeypatch, tmp_path
):
    run = worlds.run_incubation_tick(monkeypatch, tmp_path)

    _assert_surface("incubation_tick", run, min_requests=2, expect_spy=True)
    assert len(_non_spy(run)) == 1, "one INCUBATING candidate, one candidate request"
    _assert_every_body_is_calibrated(run, entry="incubation_tick")


def test_tenure_review_route_sends_the_calibrated_slippage_on_its_dated_compact_backtest(
    monkeypatch, tmp_path
):
    run = worlds.run_tenure_review_route(monkeypatch, tmp_path)

    _assert_surface("tenure_review_route", run, min_requests=1, expect_spy=False)
    assert run.outcome["status"] == 202
    first = run.backtests[0]
    assert first.kwargs.get("data") is not None, "the tenure review sends the compact text body"
    body = first.body_json()
    assert body["start_date"] and body["end_date"], "the review's backtest is dated"
    _assert_every_body_is_calibrated(run, entry="tenure_review_route")


def test_tenure_review_tick_sends_the_calibrated_slippage_on_its_dated_compact_backtest(
    monkeypatch, tmp_path
):
    run = worlds.run_tenure_review_tick(monkeypatch, tmp_path)

    _assert_surface("tenure_review_tick", run, min_requests=1, expect_spy=False)
    assert run.backtests[0].kwargs.get("data") is not None
    _assert_every_body_is_calibrated(run, entry="tenure_review_tick")


# ---------------------------------------------------------------------------
# A refused tree is repaired and retried; an unavailable SPY baseline is not re-priced
# ---------------------------------------------------------------------------


def test_a_pruned_retry_after_an_untradeable_refusal_carries_the_calibrated_slippage(
    monkeypatch, tmp_path
):
    refusal = h.Reply(status=400, body="Ticker QQQ not tradable on this venue")
    served = h.Reply(body=copy.deepcopy(worlds.GATE_RESPONSE))

    def refuse_the_first_tree(request, index):
        return refusal if index == 0 else served

    run = worlds.run_strategy_builder_route(
        monkeypatch, tmp_path, backtest_handler=refuse_the_first_tree
    )

    assert len(run.backtests) >= 2, "the compiler retried the refused tree"
    first, retry = run.backtests[0], run.backtests[1]
    assert "QQQ" in worlds.body_tickers(first), "premise: the refused tree held the named ticker"
    assert "QQQ" not in worlds.body_tickers(retry), "the retry is the pruned tree"
    assert run.router.violations == []
    assert worlds.sent_slippages(run.router)[0] == CALIBRATED_SLIPPAGE
    assert worlds.sent_slippages(run.router)[1] == CALIBRATED_SLIPPAGE
    _assert_every_body_is_calibrated(run, entry="strategy_builder_route with a refused tree")


def test_candidates_withheld_for_a_failed_spy_baseline_were_still_backtested_at_the_default(
    monkeypatch, tmp_path
):
    served = h.Reply(body=copy.deepcopy(worlds.GATE_RESPONSE))

    def spy_is_unavailable(request, index):
        if worlds.body_tickers(request) == worlds.SPY_TICKERS:
            return h.error_reply(503)
        return served

    # One plan, so the batch holds one candidate and the overfitting veto (which needs two) does
    # not decide before the SPY rule does.
    run = worlds.run_strategy_builder_route(
        monkeypatch,
        tmp_path,
        backtest_handler=spy_is_unavailable,
        plans=[worlds.sbh.conforming_diversify_plans()[0]],
    )

    spies = worlds.spy_requests(run.router)
    assert len(spies) >= 2, "the SPY baseline was retried before it gave up"
    assert len(_non_spy(run)) >= 2, "the compiler and the candidate were still backtested"
    route = run.outcome["route"]
    assert route["survivors"] == [], "an unavailable SPY series withholds every candidate"
    assert route["rejected"], "the candidates are reported, withheld"
    assert {reason for _, _, reason in route["rejected"]} == {"below_spy_alpha"}
    _assert_every_body_is_calibrated(run, entry="strategy_builder_route with SPY unavailable")


# ---------------------------------------------------------------------------
# The request sequence and the gate outcome of the ref are unchanged
# ---------------------------------------------------------------------------


def _golden() -> dict:
    return json.loads(worlds.GOLDEN_PATH.read_text(encoding="utf-8"))


def test_the_goldens_hold_a_request_sequence_for_every_entry_point():
    golden = _golden()

    assert sorted(golden) == sorted(worlds.RUNNERS)
    for entry, view in golden.items():
        assert len(view["requests"]) >= 1, f"{entry} has no recorded request"
        for request in view["requests"]:
            assert "slippage_percent" not in request["fields"], "the goldens hold no slippage"
            assert len(request["tree_sha256"]) == 64
            assert request["url"].endswith("/backtest")


@pytest.mark.parametrize("entry", list(worlds.RUNNERS))
def test_request_sequence_and_outcome_equal_the_ref_goldens(entry, monkeypatch, tmp_path):
    expected = _golden()[entry]

    run = worlds.RUNNERS[entry](monkeypatch, tmp_path)

    assert len(run.backtests) >= 1, f"{entry} sent no backtest"
    actual = json.loads(json.dumps(worlds.golden_view(run)))
    assert actual["requests"] == expected["requests"], (
        f"{entry}: the number, order, URLs or a body field other than slippage_percent differs "
        "from the ref"
    )
    assert actual["outcome"] == expected["outcome"], f"{entry}: the gate outcome differs"


def _planted_request(body: dict, form: str) -> h.Request:
    kwargs = {"data": json.dumps(body)} if form == "data" else {"json": body}
    return h.Request(
        seq=0,
        method="POST",
        url=f"{h.BASE_URL}/backtest",
        path="/backtest",
        query={},
        kind="backtest",
        match={},
        kwargs=kwargs,
    )


def _router_with(*requests: h.Request) -> h.ComposerRouter:
    router = h.ComposerRouter()
    router.requests.extend(requests)
    return router


def _planted_body(*, slippage, capital=10000.0, ids=("a1", "a2")) -> dict:
    return {
        "symphony": {
            "raw_value": {
                "id": ids[0],
                "step": "root",
                "children": [{"id": ids[1], "step": "asset"}],
            }
        },
        "capital": capital,
        "slippage_percent": slippage,
    }


@pytest.mark.parametrize("form", ["json", "data"])
def test_the_golden_masking_ignores_slippage_and_ids_but_not_any_other_field(form):
    base = worlds.masked_sequence(
        _router_with(_planted_request(_planted_body(slippage=0.005), form))
    )
    other_slippage_and_ids = worlds.masked_sequence(
        _router_with(_planted_request(_planted_body(slippage=0.001, ids=("zz-9", "zz-8")), form))
    )
    other_capital = worlds.masked_sequence(
        _router_with(_planted_request(_planted_body(slippage=0.005, capital=5000.0), form))
    )

    assert base == other_slippage_and_ids
    assert base != other_capital
    assert base[0]["body_form"] == form
    assert "slippage_percent" not in base[0]["body"]


def test_the_golden_masking_keeps_the_order_and_count_of_the_requests():
    one = _planted_request(_planted_body(slippage=0.005), "json")
    two = _planted_request(_planted_body(slippage=0.005, capital=1.0), "json")

    assert worlds.masked_sequence(_router_with(one, two)) != worlds.masked_sequence(
        _router_with(two, one)
    )
    assert worlds.masked_sequence(_router_with(one, two)) != worlds.masked_sequence(
        _router_with(one)
    )


# ---------------------------------------------------------------------------
# Stored advisory rows are only appended
# ---------------------------------------------------------------------------


def test_no_production_source_updates_or_deletes_advisor_observation_rows():
    sources = worlds.tracked_python_sources()
    assert len(sources) > 50, "premise: the scan reads the tracked production sources"

    offenders = [
        str(path.relative_to(worlds.REPO_ROOT))
        for path in sources
        if worlds.UPDATE_OR_DELETE_OF_OBSERVATIONS.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], f"these sources rewrite stored advisory rows: {offenders}"


@pytest.mark.parametrize(
    "planted",
    [
        "UPDATE advisor_observations SET verdict = 'x'",
        "update   advisor_observations set verdict = 'x'",
        "DELETE FROM advisor_observations WHERE id = 1",
        "delete\nfrom advisor_observations",
    ],
)
def test_the_observation_rewrite_scan_flags_each_rewrite_form(planted):
    assert worlds.UPDATE_OR_DELETE_OF_OBSERVATIONS.search(planted)


@pytest.mark.parametrize(
    "benign",
    [
        "SELECT * FROM advisor_observations",
        "INSERT INTO advisor_observations (advisor_role) VALUES (?)",
        "UPDATE frontrunner_proposals SET status = 'x'",
    ],
)
def test_the_observation_rewrite_scan_leaves_reads_and_appends_alone(benign):
    assert not worlds.UPDATE_OR_DELETE_OF_OBSERVATIONS.search(benign)


def test_a_second_tenure_review_tick_appends_rows_and_leaves_the_first_ticks_rows_untouched(
    monkeypatch, tmp_path
):
    worlds.run_tenure_review_tick(monkeypatch, tmp_path)
    first = worlds.advisor_observation_rows()
    assert first, "premise: the first tick wrote its rows"

    worlds.jb.freeze_app_clock(monkeypatch, worlds.SECOND_TENURE_RUN_AT)
    worlds.run_tenure_tick()
    second = worlds.advisor_observation_rows()

    assert second[: len(first)] == first, "a stored row changed, or a row was removed"
    assert len(second) > len(first), "the second tick appended its own rows"
    assert [row[0] for row in second] == sorted({row[0] for row in second}), "ids stay unique"
