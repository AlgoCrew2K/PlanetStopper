"""The preconditions route's answer is byte-identical to the unchanged route's, state by state.

Each expected body is a golden file: the response the route gave at the plan's base commit for the
state of the same name, with the ET clock frozen at Sunday 2026-11-15 12:00 (see
tests/fixtures/preconditions_replay/PROVENANCE.md and the capture script beside it). The head
builds the same state through the same helper (tests/app/_preconditions_replay_world.py) and its
bytes are compared with the golden's, so a refactor that changes a row, a key order, a float or a
status fails here. Byte equality is asserted for states, not for injected transient faults.
"""

from __future__ import annotations

import json

import pytest

import database
from tests.app._preconditions_replay_world import (
    ROUTE,
    all_degraded,
    chdir_to_tmp,  # noqa: F401  (fixture)
    golden,
    golden_world,
    install_counter,
    replay_of,
    run_together,
    server,  # noqa: F401  (fixture)
    shadow_of,
    symphony_ids,
)

pytestmark = pytest.mark.usefixtures("chdir_to_tmp")

POLL_SECONDS = 60.0


@pytest.fixture
def counter(monkeypatch):
    return install_counter(monkeypatch)


def served(world):
    resp = world.get()
    assert resp.status_code == 200, f"{ROUTE} answered {resp.status_code}"
    return resp


def assert_matches_golden(resp, name: str) -> None:
    assert resp.status_code == 200
    assert resp.data == golden(name), f"the body differs from the base route's golden {name!r}"


def test_the_golden_set_is_not_degenerate():
    """A byte comparison proves nothing when every row is empty: the full golden carries real
    replay rows for every symphony and both real and insufficient shadow verdicts."""
    body = json.loads(golden("full"))
    assert sorted(body["symphonies"]) == symphony_ids()
    assert not any(
        row["verdict"] == "INSUFFICIENT_DATA" and row["n_obs"] == 0
        for row in replay_of(body).values()
    ), "every replay row of the full golden must be a real statistics row"
    shadow_verdicts = {row["verdict"] for row in shadow_of(body).values()}
    assert "INSUFFICIENT_DATA" in shadow_verdicts
    assert len(shadow_verdicts) >= 2, (
        f"only {shadow_verdicts}: the fixture lost its real shadow rows"
    )
    assert json.loads(golden("no_file"))["symphonies"], "the no-file golden lists the symphonies"


def test_a_cold_then_a_warm_request_both_match_the_base_body(monkeypatch):
    world = golden_world("full", monkeypatch)
    assert_matches_golden(served(world), "full")
    assert_matches_golden(served(world), "full")


def test_no_file_in_the_window_matches_the_base_body(monkeypatch):
    world = golden_world("no_file", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "no_file")
    assert all_degraded(resp.get_json())


def test_a_file_lacking_one_held_symphony_degrades_that_entry_as_at_base(monkeypatch):
    world = golden_world("file_lacks_one", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "file_lacks_one")
    lacking = symphony_ids()[-1]
    rows = replay_of(resp.get_json())
    assert rows[lacking]["n_obs"] == 0
    assert all(row["n_obs"] > 0 for sid, row in rows.items() if sid != lacking)


def test_a_symphony_whose_every_date_has_no_ticks_degrades_as_at_base(monkeypatch):
    world = golden_world("empty_ticks", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "empty_ticks")
    assert replay_of(resp.get_json())[symphony_ids()[-1]]["n_obs"] == 0


def test_zero_variance_returns_keep_the_base_rows(monkeypatch):
    world = golden_world("zero_variance", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "zero_variance")
    row = replay_of(resp.get_json())[symphony_ids()[-1]]
    assert row["rho"] is None and row["sharpe_daily"] is None
    assert row["rho_ci"] is not None and row["n_obs"] > 0


def test_a_symphony_in_the_file_but_not_in_the_state_gets_no_entry(monkeypatch):
    world = golden_world("file_has_extra", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "file_has_extra")
    assert symphony_ids()[-1] not in resp.get_json()["symphonies"]


def test_a_symphony_without_shadow_rows_keeps_a_real_replay_row(monkeypatch):
    world = golden_world("no_shadow", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "no_shadow")
    entry = resp.get_json()["symphonies"][symphony_ids()[1]]
    assert entry["shadow"]["n_obs"] == 0 and entry["replay"]["n_obs"] > 0


def test_a_failing_state_read_answers_an_empty_listing_and_decodes_nothing(monkeypatch, counter):
    world = golden_world("load_state_raises", monkeypatch)
    mark = counter.mark()
    resp = served(world)
    assert counter.reads_since(mark) == 0 and counter.loader_since(mark) == 0
    assert_matches_golden(resp, "load_state_raises")
    assert resp.get_json() == {"symphonies": {}}


def test_an_empty_state_answers_an_empty_listing(monkeypatch):
    world = golden_world("load_state_empty", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "load_state_empty")
    assert resp.get_json() == {"symphonies": {}}
    assert database.load_state() == {}


def test_a_failing_shadow_read_degrades_only_that_entry(monkeypatch):
    world = golden_world("shadow_raises", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "shadow_raises")
    entry = resp.get_json()["symphonies"][symphony_ids()[1]]
    assert entry["shadow"]["n_obs"] == 0 and entry["replay"]["n_obs"] > 0


def test_metadata_keys_and_a_symphony_no_longer_held_never_appear(monkeypatch):
    world = golden_world("metadata_and_stale", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "metadata_and_stale")
    listed = set(resp.get_json()["symphonies"])
    assert listed == set(symphony_ids()) - {symphony_ids()[1]}


def test_two_polls_sixty_seconds_apart_are_byte_identical_to_each_other_and_to_base(monkeypatch):
    world = golden_world("full", monkeypatch)
    first = served(world)
    world.advance(POLL_SECONDS)
    second = served(world)
    assert_matches_golden(first, "full")
    assert_matches_golden(second, "full_plus_60s")
    assert first.data == second.data, "a closed market's answer moved between two polls"


def test_an_added_symphony_follows_the_state_and_reads_its_own_file(monkeypatch):
    world = golden_world("symphony_added", monkeypatch)
    resp = served(world)
    assert_matches_golden(resp, "symphony_added")
    body = resp.get_json()
    assert len(body["symphonies"]) == len(symphony_ids()) + 1
    assert all(row["n_obs"] > 0 for row in replay_of(body).values())


def test_two_overlapping_cold_requests_both_match_the_base_body(monkeypatch, server):
    golden_world("full", monkeypatch)
    requests = run_together(server, [ROUTE, ROUTE])
    for request in requests:
        assert request.fetched.status == 200
        assert request.fetched.data == golden("full")
