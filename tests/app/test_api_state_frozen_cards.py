"""After the close `/api/state` and `GET /` carry every card's state, values and verdict.

Before the close the poll lists each symphony as a card (`symphonies`) and stamps a guard
alpha on every triggered one. After the close the response used to carry neither: the page
rendered cards from the snapshot while the poll described none, so the first poll could only
leave the server's verdicts alone. The frozen response now builds the same list from the
snapshot's entries, and the page renders its cards from those same values.

Worlds are real: the captured exit row is written through the real writer, the close snapshot
and the live state through `database.save_state`, and the routes run against a temp SQLite
file (see `_refresh_timing_card_world`). Nothing mocks `analytics` or `database` except where a
test makes a read fail.
"""

from __future__ import annotations

import pytest

import database as database_module
from tests.app._refresh_timing_card_world import (
    DASH,
    PRE_MARKET_INSTANT,
    card_of,
    closed_cards_world,
    drawdown_percent,
    live_entry,
    move_live_state_on,
    open_cards_world,
    rendered_cards,
    seed_exit_row,
    set_live_return,
    signed_percent,
    snapshot_entry,
)
from tests.app._refresh_timing_frozen_world import et, legs_of_entries, set_clock
from tests.app._refresh_timing_state_world import (
    TRIGGERED_ID,
    UNTRIGGERED_ID,
    load_record,
    seeded_world,
)
from tests.dashboard._first_paint_dom import js_to_fixed

CARD_LEG_KEYS = ("tc_bot", "tc_held", "cr_bot", "cr_held", "mdd_bot", "mdd_held")
TRIGGERED_PILLS = {"Take-profit", "VWAP bleed", "VWAP", "Trailing stop"}
ACTIVE_STATUSES = {"triggered", "para_armed", "tp_armed", "armed"}


def _guard_alpha_of(entry_or_card: dict):
    return entry_or_card.get("guard_alpha")


def _no_guard_alpha(body: dict, symphony_id: str) -> bool:
    """True when neither the state entry nor its card carries a guard alpha."""
    return _guard_alpha_of(body["bot_state"][symphony_id]) is None and (
        _guard_alpha_of(card_of(body, symphony_id)) is None
    )


# ---------------------------------------------------------------------------
# The frozen response lists a card for every snapshot entry
# ---------------------------------------------------------------------------


class TestFrozenResponseCarriesTheCards:
    def test_each_snapshot_entry_has_a_card_with_its_status_and_legs(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()

        body = world.poll()

        assert {c["id"] for c in body["symphonies"]} == {TRIGGERED_ID, UNTRIGGERED_ID}
        assert card_of(body, TRIGGERED_ID)["status"] == "triggered"
        assert card_of(body, UNTRIGGERED_ID)["status"] == "armed"

    def test_card_legs_are_the_legs_the_frozen_entries_computed(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)

        body = world.poll()

        entry_legs = legs_of_entries(body)
        for sym_id in (TRIGGERED_ID, UNTRIGGERED_ID):
            card = card_of(body, sym_id)
            legs = entry_legs[sym_id]
            for leg, (key, side) in {
                "tc_bot": ("_tc", "dry_run"),
                "tc_held": ("_tc", "if_held"),
                "cr_bot": ("_cr", "dry_run"),
                "cr_held": ("_cr", "if_held"),
                "mdd_bot": ("_mdd", "dry_run"),
                "mdd_held": ("_mdd", "if_held"),
            }.items():
                assert card[leg] == legs[key][side], (
                    f"{sym_id} {leg}: the card does not carry the entry's {key}.{side}"
                )

    def test_every_computed_leg_is_read_by_a_card(self, monkeypatch, tmp_path):
        """The per-symphony Today, Cumulative and Max DD values the frozen branch computes
        have a reader: at least one of a symphony's legs is a real number on its card."""
        world = closed_cards_world(monkeypatch, tmp_path)

        body = world.poll()

        for sym_id in (TRIGGERED_ID, UNTRIGGERED_ID):
            card = card_of(body, sym_id)
            assert any(card[key] is not None for key in CARD_LEG_KEYS), (
                f"{sym_id}: the card carries no computed leg"
            )

    def test_each_card_carries_the_account_of_its_snapshot_entry(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)

        body = world.poll()

        for sym_id in (TRIGGERED_ID, UNTRIGGERED_ID):
            assert card_of(body, sym_id)["account"] == snapshot_entry(sym_id)["account"]

    def test_status_and_values_come_from_the_snapshot_not_from_live_state(
        self, monkeypatch, tmp_path
    ):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()
        before = world.poll()

        move_live_state_on(world)
        after = world.poll()

        assert after["symphonies"] == before["symphonies"], (
            "a card moved when only live state changed after the close"
        )
        assert card_of(after, TRIGGERED_ID)["status"] == "triggered"
        assert (
            card_of(after, TRIGGERED_ID)["current_return"]
            == snapshot_entry(TRIGGERED_ID)["current_return"]
        )

    def test_account_filter_is_ignored_and_the_account_id_is_on_each_card(
        self, monkeypatch, tmp_path
    ):
        world = closed_cards_world(monkeypatch, tmp_path)
        unfiltered = world.poll()
        account = snapshot_entry(TRIGGERED_ID)["account"]

        for query in (f"?account={account}", "?account=no-such-account-id"):
            body = world.poll(f"/api/state{query}")
            assert body["symphonies"] == unfiltered["symphonies"], query
            assert {c["account"] for c in body["symphonies"]} == {account}, query

    def test_pre_market_carries_the_same_cards_as_the_evening(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()
        evening = world.poll()

        set_clock(monkeypatch, PRE_MARKET_INSTANT)
        morning = world.poll()

        assert morning["market_state"] == "pre_market"
        assert morning["symphonies"] == evening["symphonies"]


# ---------------------------------------------------------------------------
# The guard alpha of a triggered card
# ---------------------------------------------------------------------------


class TestFrozenGuardAlpha:
    def test_triggered_entry_gets_exit_return_minus_its_snapshot_return(
        self, monkeypatch, tmp_path
    ):
        world = closed_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row()
        snapshot_return = snapshot_entry(TRIGGERED_ID)["current_return"]

        body = world.poll()

        expected = round(exit_return - snapshot_return, 6)
        assert body["bot_state"][TRIGGERED_ID]["guard_alpha"] == expected
        assert card_of(body, TRIGGERED_ID)["guard_alpha"] == expected

    def test_armed_entry_gets_no_guard_alpha(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()

        body = world.poll()

        assert _no_guard_alpha(body, UNTRIGGERED_ID)

    def test_the_figure_uses_the_snapshots_return_not_the_live_one(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row()
        snapshot_return = snapshot_entry(TRIGGERED_ID)["current_return"]
        move_live_state_on(world)
        assert live_entry(TRIGGERED_ID)["current_return"] != snapshot_return, "fixture"

        body = world.poll()

        assert body["bot_state"][TRIGGERED_ID]["guard_alpha"] == round(
            exit_return - snapshot_return, 6
        )

    def test_triggered_entry_with_no_exit_row_gets_no_guard_alpha(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)

        body = world.poll()

        assert _no_guard_alpha(body, TRIGGERED_ID)
        assert card_of(body, TRIGGERED_ID)["status"] == "triggered", "the card still exists"

    def test_entry_without_current_return_gets_no_guard_alpha(self, monkeypatch, tmp_path):
        """Never a figure computed against 0.0 for an input that is absent."""
        world = closed_cards_world(monkeypatch, tmp_path, drop_current_return=True)
        seed_exit_row()
        assert "current_return" not in snapshot_entry(TRIGGERED_ID), "fixture"

        body = world.poll()

        assert _no_guard_alpha(body, TRIGGERED_ID)
        assert card_of(body, TRIGGERED_ID)["status"] == "triggered"

    @pytest.mark.parametrize("failure", ["raises", "returns_garbage"])
    def test_failed_exit_row_read_answers_200_with_cards_and_no_guard_alpha(
        self, monkeypatch, tmp_path, failure
    ):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()

        def broken(_ids=None):
            if failure == "raises":
                raise RuntimeError("exit_triggers unreadable")
            return {TRIGGERED_ID: "not-a-number"}

        monkeypatch.setattr(database_module, "get_guard_alpha_by_symphony", broken)

        body = world.poll()

        assert {c["id"] for c in body["symphonies"]} == {TRIGGERED_ID, UNTRIGGERED_ID}
        for sym_id in (TRIGGERED_ID, UNTRIGGERED_ID):
            assert _no_guard_alpha(body, sym_id), sym_id

    def test_three_polls_on_one_snapshot_carry_identical_cards_and_figures(
        self, monkeypatch, tmp_path
    ):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()

        bodies = []
        for clock in ("18:00:00", "18:10:00", "18:20:00"):
            set_clock(monkeypatch, et("2026-10-21", clock))
            bodies.append(world.poll())

        assert bodies[0]["bot_state"][TRIGGERED_ID].get("guard_alpha") is not None, (
            "the guard alpha is part of what must stay identical"
        )
        for later in bodies[1:]:
            assert later["symphonies"] == bodies[0]["symphonies"]
            assert (
                later["bot_state"][TRIGGERED_ID]["guard_alpha"]
                == bodies[0]["bot_state"][TRIGGERED_ID]["guard_alpha"]
            )


# ---------------------------------------------------------------------------
# The page renders the cards the poll describes
# ---------------------------------------------------------------------------


def _expected_verdict_text(guard_alpha: float) -> str:
    magnitude = js_to_fixed([abs(guard_alpha)], 1)[0]
    lead = "Good call · saved +" if guard_alpha > 0 else "Early exit · gave up "
    return f"{lead}{magnitude}%α"


def _assert_page_cards_equal_poll(page: str, body: dict) -> None:
    cards = rendered_cards(page)
    assert set(cards) == {c["id"] for c in body["symphonies"]}, "the same cards on both paths"
    for card in body["symphonies"]:
        shown = cards[card["id"]]
        sym_id = card["id"]
        if card["status"] == "triggered":
            assert shown["pill"] in TRIGGERED_PILLS, (sym_id, shown["pill"])
        else:
            assert shown["pill"] == {"armed": "Armed", "standby": "Standby"}[card["status"]], sym_id
        assert shown["section"] == ("active" if card["status"] in ACTIVE_STATUSES else "standby")
        assert shown["today_bot"] == signed_percent(card["tc_bot"]), (sym_id, "today bot")
        assert shown["today_held"] == signed_percent(card["tc_held"]), (sym_id, "today held")
        if card["cr_bot"] is not None and card["cr_held"] is not None:
            assert shown["cum_bot"] == signed_percent(card["cr_bot"]), (sym_id, "cum bot")
            assert shown["cum_held"] == signed_percent(card["cr_held"]), (sym_id, "cum held")
        assert shown["mdd_bot"] == drawdown_percent(card["mdd_bot"]), (sym_id, "mdd bot")
        assert shown["mdd_held"] == drawdown_percent(card["mdd_held"]), (sym_id, "mdd held")
        ga = card["guard_alpha"]
        expected = DASH if ga is None else _expected_verdict_text(ga)
        if card["status"] == "triggered":
            assert shown["verdict_text"] == expected, (sym_id, "verdict")


class TestServerRenderEqualsThePollAfterTheClose:
    def test_each_card_of_the_page_equals_the_polls_card(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()

        page = world.page()
        body = world.poll()

        assert body["symphonies"], "the poll must list the cards the page renders"
        _assert_page_cards_equal_poll(page, body)

    def test_pre_market_cards_equal_the_first_poll_and_never_pair_two_days(
        self, monkeypatch, tmp_path
    ):
        world = closed_cards_world(monkeypatch, tmp_path, instant=PRE_MARKET_INSTANT)
        seed_exit_row()

        page = world.page()
        body = world.poll()

        assert body["market_state"] == "pre_market"
        assert body["symphonies"]
        _assert_page_cards_equal_poll(page, body)
        for card in body["symphonies"]:
            # Yesterday's Held beside a new-day Bot dash is the pairing this forbids.
            assert (card["tc_bot"] is None) == (card["tc_held"] is None), card["id"]

    def test_card_with_no_exit_row_renders_the_dash_verdict(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)

        page = world.page()
        body = world.poll()

        assert card_of(body, TRIGGERED_ID)["guard_alpha"] is None
        assert rendered_cards(page)[TRIGGERED_ID]["verdict_text"] == DASH

    def test_card_without_current_return_renders_the_dash_verdict(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path, drop_current_return=True)
        seed_exit_row()

        page = world.page()

        assert rendered_cards(page)[TRIGGERED_ID]["verdict_text"] == DASH


# ---------------------------------------------------------------------------
# The live branch keeps its figures; an absent input stays absent
# ---------------------------------------------------------------------------


class TestLiveBranchKeepsItsFiguresAndLeavesAbsentInputsAbsent:
    def test_cards_equal_the_reference_cards_for_the_same_state(self, monkeypatch, tmp_path):
        golden = load_record("golden_ref_api_state_open.json")
        world = seeded_world(monkeypatch, tmp_path, "open")

        body = world.poll()

        served = [{k: v for k, v in card.items() if k != "account"} for card in body["symphonies"]]
        assert served == golden["symphonies"]

    def test_every_live_card_carries_its_account(self, monkeypatch, tmp_path):
        world = open_cards_world(monkeypatch, tmp_path)

        body = world.poll()

        for sym_id in (TRIGGERED_ID, UNTRIGGERED_ID):
            assert card_of(body, sym_id)["account"] == live_entry(sym_id)["account"]

    def test_guard_alpha_is_exit_return_minus_live_return(self, monkeypatch, tmp_path):
        world = open_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row()
        live_return = live_entry(TRIGGERED_ID)["current_return"]

        body = world.poll()

        expected = round(exit_return - live_return, 6)
        assert body["bot_state"][TRIGGERED_ID]["guard_alpha"] == expected
        assert card_of(body, TRIGGERED_ID)["guard_alpha"] == expected
        assert _no_guard_alpha(body, UNTRIGGERED_ID)

    def test_guard_alpha_follows_the_live_return_from_cycle_to_cycle(self, monkeypatch, tmp_path):
        world = open_cards_world(monkeypatch, tmp_path)
        seed_exit_row()
        first = world.poll()["bot_state"][TRIGGERED_ID]["guard_alpha"]

        set_live_return(TRIGGERED_ID, live_entry(TRIGGERED_ID)["current_return"] + 0.5)
        second = world.poll()["bot_state"][TRIGGERED_ID]["guard_alpha"]

        assert second == pytest.approx(first - 0.5, abs=1e-6)

    def test_entry_without_current_return_gets_no_guard_alpha(self, monkeypatch, tmp_path):
        """Never a figure computed against 0.0 for an input that is absent."""
        world = open_cards_world(monkeypatch, tmp_path, drop_current_return=True)
        seed_exit_row()
        assert "current_return" not in live_entry(TRIGGERED_ID), "fixture"

        body = world.poll()

        assert _no_guard_alpha(body, TRIGGERED_ID)

    def test_page_verdict_of_an_entry_without_current_return_is_the_dash(
        self, monkeypatch, tmp_path
    ):
        world = open_cards_world(monkeypatch, tmp_path, drop_current_return=True)
        seed_exit_row()

        page = world.page()

        assert rendered_cards(page)[TRIGGERED_ID]["verdict_text"] == DASH

    def test_open_page_cards_equal_the_open_poll(self, monkeypatch, tmp_path):
        world = open_cards_world(monkeypatch, tmp_path)
        seed_exit_row()

        page = world.page()
        body = world.poll()

        _assert_page_cards_equal_poll(page, body)
