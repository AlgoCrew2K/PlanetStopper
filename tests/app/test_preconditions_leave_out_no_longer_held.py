"""The stop-justification preconditions list the symphonies that are held, and only those.

``GET /api/guard-alpha-preconditions`` is the Performance page's stop-justification panel. Every other
surface leaves out a symphony the shared rule finds no longer held
(tests/app/test_no_longer_held_surfaces.py); this one listed it, labelled by its raw id, with a
verdict of its own, and counted it in the panel's "replay samples unavailable" note.

The world is the one that file builds (two captured symphonies plus a stale copy of one, on the open
and the closed-market branch). The replay sample is withheld for every id (the cache-only reader answers nothing), so each held symphony reads
"replay unavailable" and the count the page prints is the number of held symphonies.
"""

from __future__ import annotations

import pytest

import autotuner
from tests.app import test_no_longer_held_surfaces as stale
from tests.app._refresh_timing_state_world import seeded_world

PATH = "/api/guard-alpha-preconditions"


@pytest.fixture(params=stale.BRANCHES)
def worlds(request, monkeypatch, tmp_path):
    """(the route's answer before the stale entry exists, after it was added, the held card ids)."""
    monkeypatch.setattr(autotuner, "build_if_held_replay_series", lambda symphony_id: None)
    world = seeded_world(monkeypatch, tmp_path, request.param)
    stale._seed_older_sessions(world)
    before = world.poll(PATH)
    held_cards = [card["id"] for card in world.poll("/api/state")["symphonies"]]
    stale._add_stale_entry(world)
    after = world.poll(PATH)
    return before, after, held_cards


def _replay_unavailable(body: dict) -> list[str]:
    return [sid for sid, entry in body["symphonies"].items() if entry["replay"]["n_obs"] == 0]


def test_a_symphony_no_longer_held_has_no_entry(worlds):
    _before, after, _held = worlds
    assert stale.STALE_ID not in after["symphonies"], (
        "the preconditions list a symphony that is no longer held"
    )


def test_the_entries_are_exactly_the_held_symphonies(worlds):
    before, after, held_cards = worlds
    assert set(after["symphonies"]) == set(held_cards), (
        "the preconditions do not list the symphonies the dashboard holds"
    )
    assert set(before["symphonies"]) == set(held_cards)


def test_adding_the_stale_entry_changes_nothing_in_the_answer(worlds):
    before, after, _held = worlds
    assert after == before, "the answer moved when a symphony no longer held was added"


def test_the_unavailable_replay_count_counts_held_symphonies_only(worlds):
    before, after, held_cards = worlds
    assert len(_replay_unavailable(after)) == len(held_cards), (
        "the 'replay unavailable' count includes a symphony that is no longer held"
    )
    assert len(_replay_unavailable(after)) == len(_replay_unavailable(before))
    assert len(after["symphonies"]) == len(held_cards), "the total the count is out of"
