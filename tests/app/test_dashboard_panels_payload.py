"""``GET /api/state`` carries what the server render of ``/`` shows in its Sleeves panel, CVaR panel and
Edit Vars badge, and each card's lifetime Max DD, on every branch.

Those elements were drawn by the page render alone, so a rule that fired, a CVaR diagnostic that changed
or a variable locked while ``/`` was open never reached the screen. The poll now carries them as
``dashboard_panels`` (``sleeves``, ``cvar``, ``vars_locked_count``) and each ``symphonies`` entry carries
``mdd_lifetime``; a part whose read failed is null (or ``ok`` false) so the page keeps what it shows.

Worlds are real (tests/app/_refresh_timing_state_world.py): sleeves, rules, fires, diagnostics and
strategies go in through the real ``database`` writers, and the routes run against a temp SQLite file.
Only a read that must fail is replaced. Fixture provenance: tests/fixtures/refresh_timing/03-c5/PROVENANCE.md.
"""

from __future__ import annotations

import re

import pytest

import analytics
import app as app_module
import database
from tests.app._refresh_timing_state_world import (
    BRANCHES,
    TRIGGERED_ID,
    UNTRIGGERED_ID,
    seeded_world,
)

PANEL_KEYS = {"sleeves", "cvar", "vars_locked_count"}
SLEEVE_ROW_KEYS = {
    "id",
    "name",
    "status",
    "status_badge_class",
    "capital_usd",
    "ledger_cash_usd",
    "realized_pnl_usd",
    "rules",
}
RULE_ROW_KEYS = {
    "id",
    "name",
    "mode",
    "today_fires",
    "lifetime_fires",
    "realized_pnl_usd",
    "benched",
    "stale",
}


def fire_at(world, clock: str = "14:30:00") -> str:
    """A UTC fire time inside the branch's ET trading day (14:30 UTC is 10:30 ET)."""
    return f"{world.moment.trading_day} {clock}"


def seed_sleeves(world) -> dict:
    ids = {}
    ids["sleeve"] = database.create_sleeve("Income sleeve", 1000.0, envelope_json="{}")
    ids["rule"] = database.create_sleeve_rule(ids["sleeve"], "Trim on drawdown", json_doc="{}")
    database.insert_sleeve_rule_fire(
        ids["rule"], ids["sleeve"], "buy", "ENTRY", "SHADOW", fired_at=fire_at(world)
    )
    return ids


def seed_diagnostic(cycle: str = "2026-10-21T10:43:00-04:00", cvar: float = -0.0412) -> None:
    database.record_cvar_diagnostic(
        cycle, first_symphony_id(), cvar, 0.0031, 12, cvar - 0.01, 20, mode="live"
    )


def first_symphony_id() -> str:
    state = database.load_state()
    return next(k for k, v in state.items() if isinstance(v, dict) and "name" in v)


def locked_variables_across_symphonies() -> int:
    """The total the badge counts: locked variables over every symphony in the live state."""
    state = database.load_state()
    return sum(
        len(database.get_symphony_strategy(v["name"])["locked_vars"])
        for v in state.values()
        if isinstance(v, dict) and "name" in v
    )


def panels_of(body: dict) -> dict:
    panels = body.get("dashboard_panels")
    assert isinstance(panels, dict), "the response carries no dashboard_panels"
    return panels


@pytest.mark.parametrize("branch", BRANCHES)
class TestEveryBranchCarriesThePanels:
    def test_the_response_names_the_three_parts(self, monkeypatch, tmp_path, branch):
        world = seeded_world(monkeypatch, tmp_path, branch)

        panels = panels_of(world.poll())

        assert set(panels) == PANEL_KEYS

    def test_the_sleeves_are_the_rows_the_render_builds_from_the_writers(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        ids = seed_sleeves(world)

        sleeves = panels_of(world.poll())["sleeves"]

        assert isinstance(sleeves, list) and len(sleeves) == 1
        row = sleeves[0]
        assert set(row) >= SLEEVE_ROW_KEYS
        assert row["id"] == ids["sleeve"] and row["name"] == "Income sleeve"
        assert row["capital_usd"] == 1000.0
        assert len(row["rules"]) == 1
        rule = row["rules"][0]
        assert set(rule) >= RULE_ROW_KEYS
        assert rule["id"] == ids["rule"] and rule["name"] == "Trim on drawdown"
        assert rule["mode"] == "SHADOW"
        assert rule["lifetime_fires"] == 1
        assert rule["today_fires"] == 1, (
            "today's count is the ET trading day's, as the render counts it"
        )

    def test_a_fire_written_after_the_first_poll_shows_in_the_next(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        ids = seed_sleeves(world)
        before = panels_of(world.poll())["sleeves"][0]["rules"][0]["lifetime_fires"]

        database.insert_sleeve_rule_fire(
            ids["rule"],
            ids["sleeve"],
            "sell",
            "DEFENSIVE",
            "SHADOW",
            fired_at=fire_at(world, "14:40:00"),
        )

        after = panels_of(world.poll())["sleeves"][0]["rules"][0]["lifetime_fires"]
        assert after == before + 1

    def test_a_rule_added_and_a_sleeve_deleted_show_in_the_next_poll(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        ids = seed_sleeves(world)
        second = database.create_sleeve("Growth sleeve", 2500.0, envelope_json="{}")
        panels_of(world.poll())

        added = database.create_sleeve_rule(ids["sleeve"], "Harvest losses", json_doc="{}")
        database.delete_sleeve(second)

        sleeves = panels_of(world.poll())["sleeves"]
        assert [s["id"] for s in sleeves] == [ids["sleeve"]]
        assert [r["id"] for r in sleeves[0]["rules"]] == [ids["rule"], added]

    def test_no_sleeves_is_an_empty_list_not_a_failed_read(self, monkeypatch, tmp_path, branch):
        world = seeded_world(monkeypatch, tmp_path, branch)

        assert panels_of(world.poll())["sleeves"] == []

    def test_a_failed_sleeves_read_is_null_and_the_rest_of_the_response_stands(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        seed_sleeves(world)

        def _boom(*_a, **_k):
            raise RuntimeError("sleeves table unreadable")

        monkeypatch.setattr(database, "get_all_sleeves", _boom)
        body = world.poll()

        panels = panels_of(body)
        assert panels["sleeves"] is None, "a failed read read as no sleeves would empty the panel"
        assert body["symphonies"], "the cards must not depend on the panels' reads"
        assert world.page(), "the page render still answers when the sleeves read fails"

    def test_the_cvar_part_is_empty_honestly_then_carries_the_diagnostic(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        assert panels_of(world.poll())["cvar"] == {"ok": True, "diagnostic": None}

        seed_diagnostic()

        cvar = panels_of(world.poll())["cvar"]
        assert cvar["ok"] is True
        assert cvar["diagnostic"]["cvar_5pct"] == -0.0412
        assert cvar["diagnostic"]["cvar_5pct_stderr"] == 0.0031
        assert cvar["diagnostic"]["cvar_n_tail"] == 12

    def test_a_newer_diagnostic_replaces_the_older_in_the_next_poll(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        seed_diagnostic("2026-10-21T10:43:00-04:00", -0.0412)
        panels_of(world.poll())
        import sqlite3

        with sqlite3.connect(database._db_file()) as conn:  # noqa: SLF001 - the temp database file
            conn.execute("UPDATE cvar_diagnostics SET ts_utc = '2026-10-21 14:00:00'")
        seed_diagnostic("2026-10-21T10:44:00-04:00", -0.0518)

        assert panels_of(world.poll())["cvar"]["diagnostic"]["cvar_5pct"] == -0.0518

    def test_a_failed_diagnostic_read_says_so_and_is_not_an_empty_panel(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        seed_diagnostic()

        def _boom(*_a, **_k):
            raise RuntimeError("diagnostics unreadable")

        monkeypatch.setattr(database, "read_cvar_diagnostic_for_symphony", _boom)

        cvar = panels_of(world.poll())["cvar"]

        assert cvar["ok"] is False
        assert cvar["diagnostic"] is None

    def test_the_locked_variables_count_follows_the_strategy_rows(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        name = database.load_state()[TRIGGERED_ID]["name"]
        before = panels_of(world.poll())["vars_locked_count"]
        assert before == locked_variables_across_symphonies()
        replaced = len(database.get_symphony_strategy(name)["locked_vars"])

        database.save_symphony_strategy(name, {}, ["TRAILING_STOP_PCT", "MC_EXIT_PCT", "VWAP_MULT"])

        after = panels_of(world.poll())["vars_locked_count"]
        assert after == before - replaced + 3 == locked_variables_across_symphonies()

    def test_a_panels_builder_that_raises_does_not_take_the_state_response_down(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        assert hasattr(app_module, "_dashboard_panels_payload"), "no panels builder exists"

        def _boom(*_a, **_k):
            raise RuntimeError("panels failed")

        monkeypatch.setattr(app_module, "_dashboard_panels_payload", _boom)
        response = world.client.get("/api/state")

        assert response.status_code == 200
        assert response.get_json()["symphonies"]


@pytest.mark.parametrize("branch", ["open", "closed_frozen"])
class TestEachCardCarriesItsLifetimeMaxDrawdown:
    def test_every_card_has_the_key_and_the_page_prints_the_same_figure(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        body = world.poll()
        page = world.page()

        assert body["symphonies"]
        for card in body["symphonies"]:
            assert "mdd_lifetime" in card, f"{card['id']}: the card carries no lifetime Max DD"
            value = card["mdd_lifetime"]
            expected = "--" if value is None else analytics.js_to_fixed(abs(value), 1) + "%"
            match = re.search(
                r'data-sym-id="%s".*?data-field="mdd-lifetime"[^>]*>([^<]*)<'
                % re.escape(card["id"]),
                page,
                re.S,
            )
            assert match, f"{card['id']}: the page prints no lifetime Max DD"
            assert match.group(1).strip() == expected


class TestTheServerRenderCarriesTheHooksThePollWrites:
    def test_the_sleeve_markup_is_available_to_the_page_as_two_templates(
        self, monkeypatch, tmp_path
    ):
        world = seeded_world(monkeypatch, tmp_path, "open")
        seed_sleeves(world)

        page = world.page()

        assert 'id="sleeve-card-template"' in page
        assert 'id="sleeve-rule-row-template"' in page

    def test_the_cvar_panel_is_always_rendered_and_hidden_without_a_diagnostic(
        self, monkeypatch, tmp_path
    ):
        world = seeded_world(monkeypatch, tmp_path, "open")

        page = world.page()

        panel = re.search(r"<section[^>]*id=\"cvar-diagnostic-panel\"[^>]*>", page)
        assert panel, "the CVaR panel is rendered only with a diagnostic, so a poll cannot show it"
        assert "hidden" in panel.group(0)
        assert "known-low-biased LOWER BOUND on tail severity, not a point estimate" in page

    def test_the_cvar_panel_is_shown_with_a_diagnostic_and_names_its_fields(
        self, monkeypatch, tmp_path
    ):
        world = seeded_world(monkeypatch, tmp_path, "open")
        seed_diagnostic()

        page = world.page()

        panel = re.search(r"<section[^>]*id=\"cvar-diagnostic-panel\"[^>]*>", page)
        assert panel and "hidden" not in panel.group(0)
        for field in ("cvar-5pct", "cvar-stderr", "cvar-n-tail"):
            assert f'data-field="{field}"' in page, f"no element carries data-field={field}"

    def test_the_edit_vars_badge_is_one_span_hidden_at_zero_and_shown_with_the_count(
        self, monkeypatch, tmp_path
    ):
        world = seeded_world(monkeypatch, tmp_path, "open")
        # No variable is locked: every symphony's strategy row holds an empty list.
        for symphony_id in (TRIGGERED_ID, UNTRIGGERED_ID):
            database.save_symphony_strategy(database.load_state()[symphony_id]["name"], {}, [])
        hidden = world.page()
        assert hidden.count('data-testid="edit-vars-indicator"') == 1
        empty = re.search(r"<span[^>]*data-testid=\"edit-vars-indicator\"[^>]*>", hidden).group(0)
        assert "display:none" in empty.replace(" ", "") or "hidden" in empty

        database.save_symphony_strategy(
            database.load_state()[UNTRIGGERED_ID]["name"], {}, ["TRAILING_STOP_PCT"]
        )
        shown = world.page()

        assert shown.count('data-testid="edit-vars-indicator"') == 1
        badge = re.search(
            r"<span[^>]*data-testid=\"edit-vars-indicator\"[^>]*>([^<]*)</span>", shown
        )
        assert badge and badge.group(1).strip() == "1"

    def test_the_hero_legend_names_its_mode_word_in_an_element_of_its_own(
        self, monkeypatch, tmp_path
    ):
        world = seeded_world(monkeypatch, tmp_path, "open")

        page = world.page()

        word = re.search(r'<span data-field="legend-mode">([^<]*)</span>', page)
        assert word, "the legend's mode word has no element a poll can write"
        assert word.group(1).strip() == "dry-run"
