"""`/api/state` computes the portfolio once per response, in every market state.

The dashboard reads the hero and its as-of from `meta.portfolio` and the Today, Cumulative
and Max DD rows and the headline from `portfolio_strip`. When those two come from separate
computations, a refresh of the account totals (twice a minute) or a live state that has moved
past the close snapshot lets one response show two different portfolios.

Every test drives the real routes against a seeded temp database; `analytics` and `database`
are never mocked (see `_refresh_timing_state_world`). Expected figures come from the
fixture records or from the response's own other half, never from a literal.

Seams the implementation must keep, because these tests patch or wrap them:
  * `analytics.compute_windowed_portfolio_strip`: the default-window strip call, made once
    per portfolio computation, looked up through the `analytics` module at call time.
  * `app._compute_portfolio_strip` (live computation) and `app._compute_frozen_portfolio_strip`
    (computation from the close snapshot): module-level functions looked up by global name.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import app as app_module
from tests.app._refresh_timing_state_world import (
    BRANCHES,
    count_analytics_call,
    count_app_call,
    install_account_total_drift,
    load_record,
    mask_account_totals,
    seeded_world,
)

CLOSED_BRANCHES = ("closed_frozen", "pre_market")


def _assert_meta_repeats_strip(body: dict, context: str) -> None:
    """The hero's figures (`meta.portfolio`) are the rows' figures (`portfolio_strip`)."""
    meta = body["meta"]["portfolio"]
    strip = body["portfolio_strip"]
    today = strip["today_change"]
    cumulative = strip["cumulative_return"]
    pairs = {
        "data_as_of": (meta["data_as_of"], strip["data_as_of"]),
        "guard_alpha": (meta["guard_alpha"], strip["guard_alpha"]),
        "tc": (meta["tc"], today["dry_run"]),
        "tc_if_held": (meta["tc_if_held"], today["if_held"]),
        "cr": (meta["cr"], cumulative["dry_run"]),
        "cr_if_held": (meta["cr_if_held"], cumulative["if_held"]),
        "account_value": (meta["account_value"], round(strip["account_value"], 2)),
    }
    split = {name: pair for name, pair in pairs.items() if pair[0] != pair[1]}
    assert not split, (
        f"{context}: meta.portfolio and portfolio_strip disagree inside one response "
        f"(meta value, strip value): {split}"
    )


class TestHeroAndRowsAreOneComputation:
    @pytest.mark.parametrize("branch", BRANCHES)
    def test_account_totals_refresh_landing_mid_request_cannot_split_the_response(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        install_account_total_drift(monkeypatch)

        body = world.poll()

        _assert_meta_repeats_strip(body, f"{branch}, account totals moved after the strip")

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    def test_after_close_hero_and_rows_describe_the_close_snapshot(
        self, monkeypatch, tmp_path, branch
    ):
        # The live state has moved on since the close (its returns are the later cycle's),
        # so a hero built from live state cannot match rows built from the snapshot.
        world = seeded_world(monkeypatch, tmp_path, branch)

        body = world.poll()

        _assert_meta_repeats_strip(body, f"{branch}, live state differs from the snapshot")

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_unavailable_account_totals_give_value_weighted_figures_in_both_places(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        mask_account_totals(monkeypatch)

        body = world.poll()

        assert body["portfolio_strip"].get("basis") == "value_weighted", (
            f"{branch}: with no account totals the strip must say it is value weighted"
        )
        _assert_meta_repeats_strip(body, f"{branch}, account totals unavailable")

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_two_polls_in_flight_together_each_hold_the_relations(
        self, monkeypatch, tmp_path, branch
    ):
        seeded_world(monkeypatch, tmp_path, branch)
        rendezvous = threading.Barrier(2)
        install_account_total_drift(monkeypatch, rendezvous=rendezvous)

        def poll_once() -> tuple[int, dict]:
            with app_module.app.test_client() as client:
                response = client.get("/api/state")
                return response.status_code, response.get_json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(poll_once) for _ in range(2)]
            results = [future.result(timeout=90) for future in futures]

        for status, body in results:
            assert status == 200, f"{branch}: a poll in flight answered {status}: {body}"
            _assert_meta_repeats_strip(body, f"{branch}, two polls in flight")


class TestOneComputationPerRequest:
    @pytest.mark.parametrize("branch", BRANCHES)
    def test_poll_makes_exactly_one_default_window_strip_computation(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        strips = count_analytics_call(monkeypatch, "compute_windowed_portfolio_strip")

        world.poll()

        assert strips.count == 1, (
            f"{branch}: one /api/state response ran {strips.count} portfolio-strip "
            "computations; the hero and the rows must share one"
        )

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_poll_runs_the_live_computation_only_while_the_market_is_open(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        live = count_app_call(monkeypatch, "_compute_portfolio_strip")
        frozen = count_app_call(monkeypatch, "_compute_frozen_portfolio_strip")

        world.poll()

        expected_live, expected_frozen = (1, 0) if branch == "open" else (0, 1)
        assert (live.count, frozen.count) == (expected_live, expected_frozen), (
            f"{branch}: (live, frozen) strip computations were "
            f"{(live.count, frozen.count)}, expected {(expected_live, expected_frozen)}"
        )

    @pytest.mark.parametrize("branch", CLOSED_BRANCHES)
    def test_server_render_after_close_computes_from_the_snapshot_not_live_state(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        live = count_app_call(monkeypatch, "_compute_portfolio_strip")
        frozen = count_app_call(monkeypatch, "_compute_frozen_portfolio_strip")

        world.page()

        assert (live.count, frozen.count) == (0, 1), (
            f"{branch}: GET / ran (live, frozen) strip computations {(live.count, frozen.count)}"
            ", expected (0, 1): after the close the page renders what the poll serves"
        )


class TestValuesMatchReference:
    """Every row and headline figure equals what the reference producer served for the
    same stored state. The golden files hold the strip captured from the commit this
    plan started from (see the folder's PROVENANCE.md); only the stamp is exempt, because
    its wording is a separate criterion."""

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_portfolio_strip_keeps_every_reference_figure(self, monkeypatch, tmp_path, branch):
        golden = load_record(f"golden_ref_api_state_{branch}.json")
        world = seeded_world(monkeypatch, tmp_path, branch)

        served = world.poll()["portfolio_strip"]

        changed = {
            key: (expected, served.get(key))
            for key, expected in golden["portfolio_strip"].items()
            if key != "data_as_of" and served.get(key) != expected
        }
        assert not changed, (
            f"{branch}: portfolio_strip figures differ from the reference "
            f"(reference, served): {changed}"
        )
