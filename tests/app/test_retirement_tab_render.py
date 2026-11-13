"""The Retirement tab, the Overview pointer and the exclusion sentences, rendered by the real route.

Subject: ``GET /ai-advisor`` over a real temp database. The summary rows come from the captured
droplet row or from the real recommender and writers (``tests/app/_retirement_tab_world.py`` says
which); the cards come from the real tick worker and the real approve and reject routes.

* The tab bar carries a Retirement tab right after Correlations, with its own panel; the
  Retirement section (the tenure review, the last-run block, Run recommender, the cards) lives in that
  panel and nowhere in the Overview panel.
* The last run reads as stat tiles in the dashboard's ``.mini-stats`` form with an ET stamp; every
  value is derived from the stored summary; an absent value reads the dash, never 0.
* Each recommendation is a ``.sym-card`` with a ``.status-pill``; Run recommender is a ``.run-btn``.
* Excluded symphonies read their display name and a sentence.
* The Overview carries one pointer, the last row of its observations list.
* Every test id the section had on the Overview is still present, state by state.
* Copy that names the 03:45 slot states its zone.

Fixture provenance: ``tests/fixtures/retirement/PROVENANCE.md`` and
``tests/fixtures/ai_advisor/ux_overhaul_02/PROVENANCE.md``.
"""

from __future__ import annotations

import re

import pytest

import database
from tests.advisors.test_retirement_recommender_composer_source import (
    _series_cluster_scenario,
    _series_pair_scenario,
)
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb
from tests.ui import _page_copy as copy_fixture

_GATE_WORDS = tuple(
    copy_fixture.row(key, "C")["new_text"].lower() for key in ("R09a", "R09b", "R09c", "R09d")
)
_RUN_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_NEVER_RUN_TEXT = "Not run yet \u2014 runs nightly at 03:45 UTC, or use Run recommender."


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "retirement_tab.db"))
    database.init_db()
    database.run_migrations()
    return database.DB_FILE


def _one_parse(html: str) -> pb.Node:
    """The page parsed once. ``is_inside`` walks parent links by object identity, so two nodes
    from two parses are never inside one another and ``not is_inside`` would hold for any
    markup; a containment test takes every node it compares from this one tree."""
    root = pb.parse_html(html)
    for panel in ("tab-panel-retirement", "tab-panel-overview"):
        assert root.find(panel) is not None, f"the page has no {panel}"
    return root


def _block(html: str) -> pb.Node:
    block = w.retirement_section(html).find("retirement-last-run")
    assert block is not None, "the Retirement section has no last-run block"
    return block


def _tile(block: pb.Node, testid: str) -> pb.Node:
    node = block.find(testid)
    assert node is not None, f"no element with data-testid={testid!r} in the last-run block"
    assert "mini-stat" in node.classes(), f"{testid} is not a .mini-stat: {node.classes()}"
    return node


def _part(tile: pb.Node, class_name: str) -> str:
    found = w.by_class(tile, class_name)
    assert len(found) == 1, f"{tile.testid} holds {len(found)} .{class_name} elements"
    return found[0].text()


def _numbers(text: str) -> list[str]:
    return re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.]*\w)", text)


_TILE_IDS = (
    "retirement-last-run-symphonies",
    "retirement-last-run-depth",
    "retirement-last-run-pairs",
    "retirement-last-run-blocked",
    "retirement-last-run-recommendations",
)


# ===========================================================================
# The tab and its panel
# ===========================================================================


class TestTabAndPanel:
    def test_the_tab_bar_carries_retirement_right_after_correlations(self, client, isolated_db):
        names = w.tab_names(w.get_page(client))
        assert names == [
            "overview",
            "correlations",
            "retirement",
            "asset-swaps",
            "logic-changes",
            "chat",
            "strategy-builder",
            "frontrunner-builder",
        ]

    def test_the_tab_button_follows_the_pattern_of_the_others(self, client, isolated_db):
        root = pb.parse_html(w.get_page(client))
        button = next(
            (
                n
                for n in root.walk()
                if n.attrs.get("data-tab") == "retirement" and n.tag == "button"
            ),
            None,
        )
        assert button is not None, "the tab bar has no Retirement tab button"
        assert button.attrs["role"] == "tab"
        assert button.attrs["aria-controls"] == "tab-panel-retirement"
        assert button.attrs["aria-selected"] == "false"
        assert "cap-tab" in button.classes() and "active" not in button.classes()
        assert "Retirement" in button.text()

    def test_the_panel_is_its_own_inactive_tab_panel(self, client, isolated_db):
        panel = w.tab_panel(w.get_page(client), "retirement")
        assert panel.tag == "div" and panel.attrs["id"] == "tab-panel-retirement"
        assert panel.attrs["role"] == "tabpanel" and panel.attrs["data-tab"] == "retirement"
        assert "tab-panel" in panel.classes() and "tab-panel--active" not in panel.classes()

    def test_overview_stays_the_only_active_panel(self, client, isolated_db):
        root = pb.parse_html(w.get_page(client))
        active = [n for n in root.walk() if "tab-panel--active" in n.classes()]
        assert [n.attrs["data-tab"] for n in active] == ["overview"]

    def test_every_tab_button_has_exactly_one_panel(self, client, isolated_db):
        html = w.get_page(client)
        root = pb.parse_html(html)
        panels = [n.attrs["data-tab"] for n in root.walk() if n.attrs.get("role") == "tabpanel"]
        assert sorted(panels) == sorted(w.tab_names(html))

    def test_the_section_is_inside_the_retirement_panel_and_not_the_overview(
        self, client, isolated_db
    ):
        root = _one_parse(w.get_page(client))
        section = root.find("retirement-recommendations-panel")
        assert section is not None, "the page has no retirement-recommendations-panel"
        assert section.is_inside(root.find("tab-panel-retirement"))
        assert not section.is_inside(root.find("tab-panel-overview"))

    @pytest.mark.parametrize(
        "testid",
        [
            "retirement-last-run",
            "retirement-run-now-btn",
            "retirement-run-now-status",
            "retirement-run-now-hint",
            "tenure-review-run-btn",
        ],
    )
    def test_the_controls_moved_with_the_section(self, client, isolated_db, testid):
        root = _one_parse(w.get_page(client))
        node = root.find(testid)
        assert node is not None, testid
        assert node.is_inside(root.find("tab-panel-retirement"))
        assert not node.is_inside(root.find("tab-panel-overview"))

    def test_the_old_overview_wrapper_text_is_gone_from_the_overview(self, client, isolated_db):
        html = w.get_page(client)
        overview = w.tab_panel(html, "overview")
        assert overview.find("retirement-recommendations-panel") is None
        assert overview.find("retirement-last-run") is None


# ===========================================================================
# The last run as stat tiles
# ===========================================================================


class TestBothRetirementRunRoutesRefuseWhileTheMarketIsOpen:
    """S-7.5: the refusal is the one the reference gave, from the same two routes, with the market
    open: status 409 and the body ``{"status": "market_open"}``, nothing started, no row written."""

    @pytest.mark.parametrize(
        "route", ["/ai-advisor/retirement/run", "/ai-advisor/retirement/review/run"]
    )
    def test_the_route_answers_409_market_open_and_writes_nothing(
        self, client, isolated_db, monkeypatch, route
    ):
        import app as app_module

        monkeypatch.setattr(app_module, "get_market_state", lambda *a, **k: "open")
        before = pb.observation_count()
        response = client.post(route)
        assert response.status_code == 409
        assert response.get_json() == {"status": "market_open"}
        assert pb.observation_count() == before, "a refusal wrote a row"


class TestRunTiles:
    @pytest.fixture()
    def page(self, client, isolated_db):
        w.seed_roster()
        summary = w.store_captured_summary()
        return w.get_page(client), summary

    def test_five_tiles_in_one_mini_stats_row_in_this_order(self, page):
        html, _summary = page
        block = _block(html)
        rows = w.by_class(block, "mini-stats")
        assert len(rows) == 1, "the last-run block holds one .mini-stats row"
        tiles = [n for n in rows[0].walk() if "mini-stat" in n.classes()]
        assert [t.testid for t in tiles] == list(_TILE_IDS)
        for tile in tiles:
            assert len(w.by_class(tile, "mini-stat-value")) == 1
            assert len(w.by_class(tile, "mini-stat-label")) == 1

    def test_analysed_reads_the_analysed_count_of_the_roster(self, page):
        html, s = page
        tile = _tile(_block(html), "retirement-last-run-symphonies")
        assert _part(tile, "mini-stat-label").lower().startswith("analysed")
        assert (
            _part(tile, "mini-stat-value") == f"{s['symphonies_with_series']} of {s['roster_size']}"
        )

    def test_history_reads_the_day_range(self, page):
        html, s = page
        tile = _tile(_block(html), "retirement-last-run-depth")
        assert _part(tile, "mini-stat-label").lower().startswith("history")
        assert (
            _part(tile, "mini-stat-value")
            == f"{s['series_days_min']} to {s['series_days_max']} days"
        )

    def test_pairs_reads_the_screened_count_and_the_summarys_own_threshold(self, page):
        html, s = page
        tile = _tile(_block(html), "retirement-last-run-pairs")
        label = _part(tile, "mini-stat-label")
        assert "pairs screened" in label.lower() and str(s["screen_threshold"]) in label
        assert _part(tile, "mini-stat-value") == f"{s['pairs_screened']} of {s['pairs_evaluated']}"

    def test_blocked_reads_the_total_and_lists_the_four_gate_counts_under_it(self, page):
        html, s = page
        tile = _tile(_block(html), "retirement-last-run-blocked")
        gates = [
            s["blocked_uncertainty"],
            s["blocked_stress_too_thin"],
            s["blocked_stress_below_threshold"],
            s["blocked_no_candidate"],
        ]
        assert _part(tile, "mini-stat-label").lower().startswith("blocked")
        assert _part(tile, "mini-stat-value") == str(sum(gates))
        detail = tile.text().lower()
        # The four gate words are the plan's Copy table C rows, read from the committed fixture.
        for words, count in zip(_GATE_WORDS, gates, strict=True):
            match = re.search(rf"{re.escape(words)}\D*(\d+)", detail)
            assert match, f"the Blocked detail does not name '{words}': {detail!r}"
            assert int(match.group(1)) == count, (words, detail)

    def test_blocked_no_longer_uses_the_four_old_gate_words_or_the_old_stress_sentence(self, page):
        html, s = page
        detail = _tile(_block(html), "retirement-last-run-blocked").text().lower()
        minimum = str(s["stress_gate_min_aligned_days"])
        for row in ("R09a", "R09b", "R09c", "R09d", "R09e"):
            old = copy_fixture.fill(copy_fixture.row(row, "C")["old_text"], n=minimum).lower()
            assert old not in detail, f"{row}: the old words {old!r} still show: {detail!r}"

    def test_blocked_states_the_stress_minimum_in_the_new_sentence_with_the_stored_minimum(
        self, page
    ):
        html, s = page
        assert s["blocked_stress_too_thin"] > 0
        tile = _tile(_block(html), "retirement-last-run-blocked")
        sentence = copy_fixture.fill(
            copy_fixture.row("R09e", "C")["new_text"], n=str(s["stress_gate_min_aligned_days"])
        )
        assert " ".join(sentence.split()) in " ".join(tile.text().split())

    def test_blocked_states_the_stress_minimum_from_the_summary_when_a_pair_was_too_thin(
        self, page
    ):
        html, s = page
        assert s["blocked_stress_too_thin"] > 0
        tile = _tile(_block(html), "retirement-last-run-blocked")
        assert str(s["stress_gate_min_aligned_days"]) in _numbers(tile.text())

    def test_blocked_does_not_state_the_minimum_when_no_pair_was_too_thin(
        self, client, isolated_db
    ):
        w.seed_roster()
        s = w.derived_summary(blocked_stress_too_thin=0, blocked_uncertainty=10)
        w.store_raw_summary(s)
        tile = _tile(_block(w.get_page(client)), "retirement-last-run-blocked")
        assert str(s["stress_gate_min_aligned_days"]) not in _numbers(tile.text())

    def test_recommendations_reads_the_count(self, page):
        html, s = page
        tile = _tile(_block(html), "retirement-last-run-recommendations")
        assert _part(tile, "mini-stat-label").lower().startswith("recommendations")
        assert _part(tile, "mini-stat-value") == str(s["recommendations"])

    def test_the_stamp_reads_last_run_with_the_et_time_as_a_legend_stamp(self, page):
        html, s = page
        block = _block(html)
        stamp = block.find("retirement-last-run-time")
        assert stamp is not None and "legend-as-of" in stamp.classes()
        # 18:50:08 UTC on 2026-10-22 is 14:50 in New York (summer time, UTC-4).
        expected = w.et_text(s["run_at"], _RUN_AT_FORMAT, -4)
        assert stamp.text() == f"Last run {expected}"
        assert "UTC" not in stamp.text()
        assert not stamp.is_inside(w.by_class(block, "mini-stats")[0])

    def test_the_basis_line_reads_the_summarys_basis_label(self, page):
        html, s = page
        basis = _block(html).find("retirement-last-run-basis")
        assert basis is not None
        assert "Composer realized daily returns" in basis.text(), basis.text()
        assert not re.search(r"composer", basis.text()), (
            "the internal source word 'composer' is still on the basis line"
        )

    @pytest.mark.parametrize("how", ["absent", "null"])
    @pytest.mark.parametrize(
        ("key", "testid"),
        [
            ("symphonies_with_series", "retirement-last-run-symphonies"),
            ("series_days_min", "retirement-last-run-depth"),
            ("pairs_screened", "retirement-last-run-pairs"),
            ("recommendations", "retirement-last-run-recommendations"),
            ("blocked_uncertainty", "retirement-last-run-blocked"),
            ("blocked_stress_too_thin", "retirement-last-run-blocked"),
            ("blocked_stress_below_threshold", "retirement-last-run-blocked"),
            ("blocked_no_candidate", "retirement-last-run-blocked"),
        ],
    )
    def test_a_missing_value_reads_the_dash_never_zero(self, client, isolated_db, key, testid, how):
        w.seed_roster()
        summary = w.without_keys(key) if how == "absent" else w.derived_summary(**{key: None})
        w.store_raw_summary(summary)
        block = _block(w.get_page(client))
        assert _part(_tile(block, testid), "mini-stat-value") == w.DASH
        # The other tiles are unaffected by it.
        for other in _TILE_IDS:
            if other != testid:
                assert _part(_tile(block, other), "mini-stat-value") != w.DASH, other


# ===========================================================================
# Every state
# ===========================================================================


class TestStates:
    def test_never_run_says_so_with_the_zone_and_shows_no_tile(self, client, isolated_db):
        block = _block(w.get_page(client))
        assert _NEVER_RUN_TEXT in block.text()
        assert w.by_class(block, "mini-stat") == [] and w.by_class(block, "mini-stats") == []
        for testid in ("retirement-last-run-time", "retirement-last-run-basis", *_TILE_IDS):
            assert block.find(testid) is None, testid

    def test_series_unavailable_keeps_its_copy_and_names_the_analysed_count(
        self, client, isolated_db, monkeypatch
    ):
        summary = w.run_recommender_all_requests_refused(monkeypatch)
        w.store_summary(summary)
        block = _block(w.get_page(client))
        assert "series unavailable" in block.text().lower()
        analysed = block.find("retirement-last-run-symphonies")
        assert analysed is not None
        assert str(summary["symphonies_with_series"]) in _numbers(analysed.text())
        assert str(summary["roster_size"]) in _numbers(analysed.text())
        assert _NEVER_RUN_TEXT not in block.text()
        assert block.find("retirement-last-run-time") is not None

    def test_insufficient_symphonies_keeps_its_copy(self, client, isolated_db, monkeypatch):
        summary = w.run_recommender_one_symphony(monkeypatch)
        assert summary["status"] == "insufficient_symphonies"
        w.store_summary(summary)
        block = _block(w.get_page(client))
        assert "insufficient symphonies" in block.text().lower()
        assert _NEVER_RUN_TEXT not in block.text()

    def test_internal_error_keeps_its_copy_and_names_the_error_type(
        self, client, isolated_db, monkeypatch
    ):
        w.store_summary(w.internal_error_summary(monkeypatch))
        block = _block(w.get_page(client))
        assert "internal error" in block.text().lower() and "ConnectionError" in block.text()
        assert _NEVER_RUN_TEXT not in block.text()
        assert block.find("retirement-last-run-time") is not None

    def test_a_failure_state_shows_no_tiles_that_the_run_did_not_produce(
        self, client, isolated_db, monkeypatch
    ):
        w.store_summary(w.internal_error_summary(monkeypatch))
        block = _block(w.get_page(client))
        for testid in ("retirement-last-run-pairs", "retirement-last-run-blocked"):
            assert block.find(testid) is None, testid

    def test_a_failed_night_after_a_good_run_notes_the_earlier_runs_et_date(
        self, client, isolated_db, monkeypatch
    ):
        w.seed_roster()
        ok = w.store_captured_summary()
        w.store_summary(w.internal_error_summary(monkeypatch))
        note = w.retirement_section(w.get_page(client)).find("retirement-cards-from-earlier-run")
        assert note is not None, "the earlier-run note is missing"
        assert ok["run_at"][:10] in note.text()  # 14:50 ET is the same date
        assert "UTC" not in note.text()

    def test_the_earlier_runs_date_is_its_et_date_not_its_utc_date(
        self, client, isolated_db, monkeypatch
    ):
        # 02:30 UTC on 2026-10-23 is 22:30 on 2026-10-22 in New York.
        summary = w.run_ok_summary(monkeypatch, _series_pair_scenario(), "2026-10-23T02:30:00Z")
        w.store_raw_summary(summary)
        w.store_summary(w.internal_error_summary(monkeypatch))
        note = w.retirement_section(w.get_page(client)).find("retirement-cards-from-earlier-run")
        assert note is not None
        assert "2026-10-22" in note.text() and "2026-10-23" not in note.text()

    def test_no_note_when_the_latest_run_is_the_cards_run(self, client, isolated_db):
        w.seed_roster()
        w.store_captured_summary()
        section = w.retirement_section(w.get_page(client))
        assert section.find("retirement-cards-from-earlier-run") is None


# ===========================================================================
# Cards, pills, Run now
# ===========================================================================


class TestCardsAndControls:
    @pytest.fixture()
    def cards_page(self, client, isolated_db, monkeypatch):
        summary = w.run_tick_with_recommendations(monkeypatch, _series_cluster_scenario())
        ids = w.stored_recommendation_ids()
        assert summary["status"] == "ok" and summary["recommendations"] == len(ids) == 2
        return client, ids

    def test_run_now_is_a_secondary_run_button_with_its_hint_and_status(self, client, isolated_db):
        section = w.retirement_section(w.get_page(client))
        button = section.find("retirement-run-now-btn")
        assert button.tag == "button"
        assert "run-btn" in button.classes() and "discuss-btn" not in button.classes()
        assert button.attrs["onclick"] == "retirementRunNow(this)"
        status = section.find("retirement-run-now-status")
        assert status.attrs["role"] == "status"
        hint = section.find("retirement-run-now-hint")
        assert "outside market hours" in hint.text()

    def test_each_recommendation_is_a_sym_card_naming_its_pair(self, cards_page):
        client, ids = cards_page
        section = w.retirement_section(w.get_page(client))
        cards = [
            c
            for c in section.find_all("retirement-recommendation-card")
            if "sym-card" in c.classes()
        ]
        assert len(cards) == len(ids)
        for card in cards:
            names = w.by_class(card, "card-name")
            assert len(names) == 1
            assert any(candidate in names[0].text() for candidate in ids)

    def test_a_pending_card_shows_a_standby_status_pill(self, cards_page):
        client, ids = cards_page
        section = w.retirement_section(w.get_page(client))
        pills = section.find_all("retirement-rec-status")
        assert len(pills) == len(ids)
        for pill in pills:
            assert {"status-pill", "standby"} <= set(pill.classes()), pill.classes()
            assert pill.text() == "Pending"
            assert "retirement-status-chip" not in " ".join(pill.classes())

    def test_an_approved_card_shows_an_armed_pill_and_the_checklist_in_the_tab(self, cards_page):
        client, ids = cards_page
        assert w.decide(client, "approve", ids[0]).get("success") is True
        root = _one_parse(w.get_page(client))
        section = root.find("retirement-recommendations-panel")
        assert section is not None, "the page has no retirement-recommendations-panel"
        pills = {p.text(): p for p in section.find_all("retirement-rec-status")}
        assert {"status-pill", "armed"} <= set(pills["Approved"].classes())
        assert {"status-pill", "standby"} <= set(pills["Pending"].classes())
        checklist = section.find("retirement-checklist")
        assert checklist is not None and checklist.is_inside(root.find("tab-panel-retirement"))
        assert not checklist.is_inside(root.find("tab-panel-overview"))

    def test_a_rejected_card_shows_a_triggered_pill(self, cards_page):
        client, ids = cards_page
        assert w.decide(client, "reject", ids[1]).get("success") is True
        section = w.retirement_section(w.get_page(client))
        pills = {p.text(): p for p in section.find_all("retirement-rec-status")}
        assert {"status-pill", "triggered"} <= set(pills["Rejected"].classes())

    def test_the_card_keeps_its_evidence_ids(self, cards_page):
        client, _ids = cards_page
        section = w.retirement_section(w.get_page(client))
        for testid in (
            "retirement-rec-correlation",
            "retirement-rec-explanation",
            "retirement-approve-btn",
            "retirement-reject-btn",
        ):
            assert section.find(testid) is not None, testid

    def test_the_page_local_chip_rules_are_gone(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        for rel in ("templates/ai_advisor.html", "static/ai_advisor.js"):
            text = (root / rel).read_text(encoding="utf-8")
            assert "retirement-status-chip" not in text, f"{rel} still carries the old chip class"

    def test_the_status_pill_classes_are_the_same_set_the_script_swaps_to(self, cards_page):
        # The server map and the script's map must agree (one pill per decision state).
        import pathlib

        client, ids = cards_page
        js = (pathlib.Path(__file__).resolve().parents[2] / "static" / "ai_advisor.js").read_text(
            encoding="utf-8"
        )
        for state, pill in (
            ("pending", "standby"),
            ("approved", "armed"),
            ("rejected", "triggered"),
        ):
            assert re.search(rf"{state}['\"]?\s*:\s*['\"]{pill}['\"]", js), (state, pill)


# ===========================================================================
# Exclusion sentences, rendered
# ===========================================================================

_SENTENCES = {
    "no_account": "No Composer account is recorded for this symphony.",
    "series_too_short": "Its Composer series has fewer than 2 daily returns.",
    "shadow_series_unavailable": "No shadow history is available for it.",
}


def _excluded(html: str) -> pb.Node:
    node = w.retirement_section(html).find("retirement-last-run-excluded")
    assert node is not None, "the last-run block lists no excluded symphonies"
    return node


def _entries(excluded: pb.Node) -> list[pb.Node]:
    return [n for n in excluded.walk() if n.tag == "li"]


class TestExclusionSentences:
    def test_a_captured_exclusion_reads_the_display_name_and_its_sentence(
        self, client, isolated_db
    ):
        w.seed_roster()
        w.store_captured_summary()
        (entry,) = _entries(_excluded(w.get_page(client)))
        assert w.EXCLUDED_NAME in entry.text()
        assert _SENTENCES["series_too_short"] in entry.text()
        assert "series_too_short" not in entry.text()
        assert entry.attrs.get("title") == w.EXCLUDED_ID, "the hash stays in the title only"
        assert w.EXCLUDED_ID not in entry.text()

    def test_a_symphony_no_longer_in_the_state_reads_its_hash_with_the_sentence(
        self, client, isolated_db
    ):
        w.seed_roster(without=(w.EXCLUDED_ID,))
        w.store_captured_summary()
        (entry,) = _entries(_excluded(w.get_page(client)))
        assert w.EXCLUDED_ID in entry.text()
        assert _SENTENCES["series_too_short"] in entry.text()

    def test_the_fetch_error_and_missing_account_reasons_come_from_the_real_recommender(
        self, client, isolated_db, monkeypatch
    ):
        summary = w.run_recommender_mixed_exclusions(monkeypatch)
        reasons = {e["symphony_id"]: e["reason"] for e in summary["symphonies_without_series"]}
        assert sorted(reasons.values()) == [
            "fetch_error: HTTP 429",
            "fetch_error: HTTP 429",
            "no_account",
        ]
        w.store_summary(summary)
        texts = [e.text() for e in _entries(_excluded(w.get_page(client)))]
        assert len(texts) == 3
        assert sum(_SENTENCES["no_account"] in t for t in texts) == 1
        assert sum("Its Composer series could not be fetched (HTTP 429)." in t for t in texts) == 2
        assert not any("fetch_error" in t or "no_account" in t for t in texts)

    def test_the_shadow_reason_comes_from_the_real_recommender(
        self, client, isolated_db, monkeypatch
    ):
        summary = w.run_recommender_shadow_without_history(monkeypatch)
        assert {e["reason"] for e in summary["symphonies_without_series"]} == {
            "shadow_series_unavailable"
        }
        w.store_summary(summary)
        texts = [e.text() for e in _entries(_excluded(w.get_page(client)))]
        assert texts and all(_SENTENCES["shadow_series_unavailable"] in t for t in texts)

    def test_a_reason_not_in_the_mapping_reads_excluded_and_the_raw_reason_escaped(
        self, client, isolated_db
    ):
        w.seed_roster()
        raw = "unexpected <b>reason</b> & more"
        summary = w.derived_summary(
            symphonies_without_series=[{"symphony_id": w.EXCLUDED_ID, "reason": raw}]
        )
        w.store_raw_summary(summary)
        html = w.get_page(client)
        (entry,) = _entries(_excluded(html))
        assert f"Excluded: {raw}" in entry.text()
        assert (
            "<b>reason</b>"
            not in html.split("retirement-last-run-excluded", 1)[1].split("</ul>", 1)[0]
        )

    @pytest.mark.parametrize("empty", ["", None, 5, ["x"]])
    def test_an_empty_or_non_text_reason_reads_reason_not_recorded(
        self, client, isolated_db, empty
    ):
        w.seed_roster()
        summary = w.derived_summary(
            symphonies_without_series=[{"symphony_id": w.EXCLUDED_ID, "reason": empty}]
        )
        w.store_raw_summary(summary)
        (entry,) = _entries(_excluded(w.get_page(client)))
        assert w.EXCLUDED_NAME in entry.text() and "Reason not recorded." in entry.text()

    def test_rendering_leaves_the_stored_summary_byte_identical(self, client, isolated_db):
        w.seed_roster()
        w.store_captured_summary()
        before = w.stored_summary_json()
        w.get_page(client)
        w.get_page(client)
        assert w.stored_summary_json() == before


# ===========================================================================
# The Retirement row of the observations list (the pointer that left the toolbar)
# ===========================================================================

# The stored run of the scenario: 03:51 UTC on 2026-11-10 is 22:51 Eastern on 2026-11-09 (standard time).
_ROW_RUN_AT = "2026-11-10T03:51:00Z"
_NEVER_RUN_STATUS = "not run yet (use Run recommender)"


def _retirement_rows(html: str, panel: pb.Node | None = None) -> list[pb.Node]:
    """The Retirement rows of the Overview; pass the panel already parsed when nodes of that tree
    are compared by identity (a second parse is a different tree)."""
    panel = panel if panel is not None else w.tab_panel(html, "overview")
    return [
        n
        for n in panel.walk()
        if "obs-producer" in n.classes() and n.attrs.get("data-role") == "RETIREMENT"
    ]


def _retirement_row(html: str, panel: pb.Node | None = None) -> pb.Node:
    rows = _retirement_rows(html, panel)
    assert len(rows) == 1, f"the Overview holds {len(rows)} Retirement rows, not one"
    return rows[0]


def _cell(row: pb.Node, class_name: str) -> pb.Node:
    cells = w.by_class(row, class_name)
    assert len(cells) == 1, f"the Retirement row holds {len(cells)} .{class_name} cells, not one"
    return cells[0]


def _pointer(html: str) -> pb.Node:
    node = _retirement_row(html).find("retirement-pointer")
    assert node is not None, "the Retirement row carries no pointer link"
    return node


def _status(html: str) -> str:
    """The verdict cell of the Retirement row: the pointer text without its leading word."""
    return _cell(_retirement_row(html), "obs-producer-verdict").text()


class TestRetirementRow:
    def test_it_is_the_last_row_of_the_observations_list_and_the_only_pointer(
        self, client, isolated_db
    ):
        from tests._advisor_observation_records import store

        w.seed_roster()
        store("guardrail_clear", times=2)
        store("guardrail_watch", times=2)
        html = w.get_page(client)
        panel = w.tab_panel(html, "overview")
        section = panel.find("advisor-observations-section")
        row = _retirement_row(html, panel)
        assert row.is_inside(section), "the Retirement row is outside the observations list"
        order = list(panel.walk())
        others = [n for n in order if "obs-producer" in n.classes() and n is not row]
        assert len(others) >= 2, "premise: producer rows are on the page"
        assert all(order.index(n) < order.index(row) for n in others), (
            "the Retirement row is not the last row of the list"
        )
        assert len(pb.parse_html(html).find_all("retirement-pointer")) == 1, (
            "more than one Retirement pointer on the page"
        )

    def test_the_toolbar_holds_no_pointer_and_nothing_is_styled_as_one(self, client, isolated_db):
        html = w.get_page(client)
        panel = w.tab_panel(html, "overview")
        grid = panel.find("advisor-grid")
        assert grid is not None
        assert grid.find("retirement-pointer") is None, "the pointer is still on the toolbar"
        assert not [n for n in panel.walk() if "adv-pointer" in n.classes()], (
            "an element still carries the toolbar pointer class"
        )
        assert not [n for n in grid.walk() if n.tag == "a" and n.attrs.get("href") == "#retirement"]

    def test_a_row_without_other_producers_still_shows_after_the_empty_line(
        self, client, isolated_db
    ):
        html = w.get_page(client)
        panel = w.tab_panel(html, "overview")
        empty = panel.find("obs-empty")
        assert empty is not None, "premise: no observations stored"
        order = list(panel.walk())
        assert order.index(empty) < order.index(_retirement_row(html, panel))

    def test_its_label_verdict_and_link_read_as_the_plan_words_with_empty_time_and_count_cells(
        self, client, isolated_db
    ):
        w.seed_roster()
        w.store_raw_summary(w.derived_summary(run_at=_ROW_RUN_AT, recommendations=0))
        html = w.get_page(client)
        row = _retirement_row(html)
        assert _cell(row, "obs-producer-label").text() == "Retirement"
        assert _cell(row, "obs-producer-verdict").text() == (
            "last run 2026-11-09 22:51 ET, 0 recommendations, 0 pending"
        )
        assert _cell(row, "obs-producer-time").text() == ""
        assert _cell(row, "obs-producer-total").text() == ""
        link = _pointer(html)
        assert link.tag == "a" and link.attrs["href"] == "#retirement"
        assert link.text() == "Open tab"

    def test_the_verdict_cell_never_repeats_the_leading_word(self, client, isolated_db):
        w.seed_roster()
        w.store_raw_summary(w.derived_summary(run_at=_ROW_RUN_AT, recommendations=0))
        status = _status(w.get_page(client))
        assert not status.startswith("Retirement") and "Retirement:" not in status

    def test_a_row_that_never_ran_reads_not_run_yet(self, client, isolated_db):
        assert _status(w.get_page(client)) == _NEVER_RUN_STATUS

    def test_a_good_run_reads_its_et_time_its_count_and_the_pending_count(
        self, client, isolated_db, monkeypatch
    ):
        summary = w.run_tick_with_recommendations(monkeypatch, _series_cluster_scenario())
        ids = w.stored_recommendation_ids()
        text = _status(w.get_page(client))
        assert w.et_text(summary["run_at"], _RUN_AT_FORMAT, -4) in text
        assert f"{summary['recommendations']} recommendations" in text
        assert f"{len(ids)} pending" in text
        # One decision lowers the pending count and nothing else.
        w.decide(client, "approve", ids[0])
        text = _status(w.get_page(client))
        assert f"{summary['recommendations']} recommendations" in text
        assert f"{len(ids) - 1} pending" in text

    @pytest.mark.parametrize(
        ("builder", "state_words"),
        [
            (lambda w_, mp: w_.internal_error_summary(mp), "internal error"),
            (lambda w_, mp: w_.run_recommender_all_requests_refused(mp), "series unavailable"),
            (lambda w_, mp: w_.run_recommender_one_symphony(mp), "insufficient symphonies"),
        ],
        ids=["internal_error", "series_unavailable", "insufficient_symphonies"],
    )
    def test_a_failed_run_names_its_state_and_its_time(
        self, client, isolated_db, monkeypatch, builder, state_words
    ):
        summary = builder(w, monkeypatch)
        w.store_summary(summary)
        text = _status(w.get_page(client))
        assert text.startswith(f"last run {state_words}, ")
        assert w.et_text(summary["run_at"], _RUN_AT_FORMAT, -4) in text

    def test_the_row_reads_context_the_page_already_has(self, client, isolated_db, monkeypatch):
        # One read of each retirement source per page render, as before the pointer moved.
        import app as app_module

        w.seed_roster()
        w.store_captured_summary()
        calls = {"last_run": 0, "cards_run": 0, "recommendations": 0}
        for attr, key in (
            ("_fetch_retirement_last_run", "last_run"),
            ("_fetch_retirement_cards_run", "cards_run"),
            ("_fetch_retirement_recommendations", "recommendations"),
        ):
            original = getattr(app_module, attr)

            def spy(*a, _orig=original, _key=key, **k):
                calls[_key] += 1
                return _orig(*a, **k)

            monkeypatch.setattr(app_module, attr, spy)
        w.get_page(client)
        assert calls == {"last_run": 1, "cards_run": 1, "recommendations": 1}


class TestRetirementRowSecurity:
    def test_a_stored_state_carrying_markup_is_escaped_in_the_verdict_cell(
        self, client, isolated_db
    ):
        w.seed_roster()
        hostile = "<script>alert(1)</script>"
        w.store_raw_summary(w.derived_summary(run_at=_ROW_RUN_AT, status=hostile))
        html = w.get_page(client)
        assert hostile not in html, "the stored state reached the page as markup"
        assert "alert(1)" in _status(html), "premise: the state is shown, as text"


class TestRetirementPointerStatus:
    """``_build_retirement_pointer`` returns ``status`` alone, the one key the Retirement row of the
    observations list reads; the full text it once carried had no reader and is gone."""

    def test_never_run_has_a_status(self):
        import app as app_module

        got = app_module._build_retirement_pointer(None, [])
        assert got["status"] == _NEVER_RUN_STATUS

    def test_a_good_run_status_is_the_time_the_count_and_the_pending_count(self):
        import app as app_module

        got = app_module._build_retirement_pointer(
            {"run_at": _ROW_RUN_AT, "status": "ok", "recommendations": 2},
            [{"approval_status": "pending"}, {"approval_status": "approved"}],
        )
        assert got["status"] == "last run 2026-11-09 22:51 ET, 2 recommendations, 1 pending"

    def test_one_recommendation_reads_in_the_singular(self):
        import app as app_module

        got = app_module._build_retirement_pointer(
            {"run_at": _ROW_RUN_AT, "status": "ok", "recommendations": 1}, []
        )
        assert got["status"] == "last run 2026-11-09 22:51 ET, 1 recommendation, 0 pending"

    def test_a_run_that_did_not_finish_names_its_state_and_its_time(self):
        import app as app_module

        got = app_module._build_retirement_pointer(
            {"run_at": _ROW_RUN_AT, "status": "internal_error"}, []
        )
        assert got["status"] == "last run internal error, 2026-11-09 22:51 ET"

    @pytest.mark.parametrize("status", [None, "", "  ", 5])
    def test_a_run_with_no_readable_state_reads_unknown(self, status):
        import app as app_module

        got = app_module._build_retirement_pointer({"run_at": _ROW_RUN_AT, "status": status}, [])
        assert got["status"] == "last run unknown, 2026-11-09 22:51 ET"

    @pytest.mark.parametrize(
        "row",
        [None, {"run_at": _ROW_RUN_AT, "status": "ok", "recommendations": 2}],
        ids=["never run", "a good run"],
    )
    def test_the_value_carries_only_the_key_the_page_reads(self, row):
        """S-7.18: no key without a reader. The Retirement row reads ``status`` and nothing else
        (the template, the scripts and the routes read no other), so the value holds it alone."""
        import app as app_module

        assert set(app_module._build_retirement_pointer(row, [])) == {"status"}

    def test_the_template_reads_no_other_key_of_the_value(self):
        """The set above is the set the template reads: every attribute it takes of the context
        value is ``status``."""
        import pathlib

        template = (
            pathlib.Path(__file__).resolve().parents[2] / "templates" / "ai_advisor.html"
        ).read_text(encoding="utf-8")
        read = set(re.findall(r"retirement_pointer\.(\w+)", template))
        read |= set(re.findall(r"retirement_pointer\[\s*['\"](\w+)['\"]", template))
        read |= set(re.findall(r"retirement_pointer\.get\(\s*['\"](\w+)['\"]", template))
        assert read == {"status"}, read


# ===========================================================================
# Every test id of the section, state by state
# ===========================================================================


class TestSectionTestIdsSurvive:
    _ALWAYS = (
        "retirement-recommendations-panel",
        "retirement-last-run",
        "retirement-run-now-btn",
        "retirement-run-now-status",
        "retirement-run-now-hint",
    )
    _OK_BLOCK = (
        "retirement-last-run-time",
        "retirement-last-run-basis",
        "retirement-last-run-symphonies",
        "retirement-last-run-depth",
        "retirement-last-run-pairs",
        "retirement-last-run-blocked",
        "retirement-last-run-recommendations",
        "retirement-last-run-excluded",
    )

    def _present(self, html: str, ids) -> None:
        root = pb.parse_html(html)
        missing = [i for i in ids if root.find(i) is None]
        assert missing == [], missing

    def test_never_run(self, client, isolated_db):
        self._present(w.get_page(client), self._ALWAYS)

    def test_ok_run_with_an_exclusion(self, client, isolated_db):
        w.seed_roster()
        w.store_captured_summary()
        self._present(w.get_page(client), (*self._ALWAYS, *self._OK_BLOCK))

    def test_a_failed_night_after_a_good_run(self, client, isolated_db, monkeypatch):
        w.seed_roster()
        w.store_captured_summary()
        w.store_summary(w.internal_error_summary(monkeypatch))
        self._present(w.get_page(client), (*self._ALWAYS, "retirement-cards-from-earlier-run"))

    def test_cards_in_every_decision_state(self, client, isolated_db, monkeypatch):
        w.run_tick_with_recommendations(monkeypatch, _series_cluster_scenario())
        ids = w.stored_recommendation_ids()
        w.decide(client, "approve", ids[0])
        self._present(
            w.get_page(client),
            (
                *self._ALWAYS,
                "retirement-recommendation-card",
                "retirement-rec-correlation",
                "retirement-rec-explanation",
                "retirement-rec-status",
                "retirement-approve-btn",
                "retirement-reject-btn",
                "retirement-checklist",
            ),
        )

    def test_no_review_and_no_cards_keeps_the_empty_marker(self, client, isolated_db):
        self._present(w.get_page(client), (*self._ALWAYS, "retirement-recommendations-empty"))


# ===========================================================================
# The zone of every copy that names the 03:45 slot
# ===========================================================================


class TestScheduleZone:
    @pytest.mark.parametrize("state", ["never", "ok"])
    def test_every_mention_of_the_slot_in_the_tab_states_its_zone(self, client, isolated_db, state):
        if state == "ok":
            w.seed_roster()
            w.store_captured_summary()
        panel = w.tab_panel(w.get_page(client), "retirement")
        # Text and every attribute value (a button title, a tooltip) in the tab.
        bare = re.findall(r"03:45(?!\s*UTC)", panel.everything())
        assert bare == [], "a mention of the 03:45 slot names no zone (the droplet clock is UTC)"

    def test_the_never_run_copy_names_the_slot_in_utc(self, client, isolated_db):
        assert "runs nightly at 03:45 UTC" in w.retirement_section(w.get_page(client)).text()


# ===========================================================================
# The tenure review and the recommender share the tab
# ===========================================================================


class TestMergedPanel:
    @pytest.fixture()
    def merged(self, client, isolated_db):
        pb.seed_bot_state()
        pb.store_full_run()
        w.store_captured_summary()
        return w.get_page(client)

    def test_the_review_and_the_last_run_block_share_the_retirement_panel(self, merged):
        panel = w.tab_panel(merged, "retirement")
        assert panel.find("tenure-review-run-btn") is not None
        assert panel.find("tenure-review-entry") is not None
        assert panel.find("retirement-last-run") is not None
        assert panel.find("retirement-run-now-btn") is not None

    def test_neither_is_left_in_the_overview(self, merged):
        overview = w.tab_panel(merged, "overview")
        for testid in ("tenure-review-entry", "tenure-review-run-btn", "retirement-last-run"):
            assert overview.find(testid) is None, testid

    def test_the_review_entrys_decision_state_is_a_status_pill_too(self, merged, client):
        entry_ids = [
            e.attrs["data-symphony-id"]
            for e in w.tab_panel(merged, "retirement").find_all("tenure-review-entry")
        ]
        assert entry_ids
        retire = pb.FULL_IDS["retire"]
        assert retire in entry_ids
        w.decide(client, "approve", retire)
        html = w.get_page(client)
        node = pb.entry_node(w.tab_panel(html, "retirement"), retire)
        pill = node.find("retirement-rec-status")
        assert pill is not None
        assert {"status-pill", "armed"} <= set(pill.classes()) and pill.text() == "Approved"
        checklist = node.find("retirement-checklist")
        assert checklist is not None

    def test_the_review_timestamps_and_the_run_stamp_use_one_et_form(self, merged):
        panel = w.tab_panel(merged, "retirement")
        run_time = panel.find("tenure-review-run-time")
        assert run_time is not None
        assert re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d ET", run_time.text())
        stamp = panel.find("retirement-last-run-time")
        assert re.fullmatch(r"Last run \d{4}-\d\d-\d\d \d\d:\d\d ET", stamp.text())

    def test_the_pointer_counts_the_review_candidates_waiting_for_a_decision(self, merged, client):
        # The tab lists a retire candidate from the symphony review that waits for a decision. The
        # pointer states the recommender's count of 0 and must not say nothing is pending.
        text = _status(merged)
        assert "0 recommendations" in text and "1 pending" in text
        w.decide(client, "approve", pb.FULL_IDS["retire"])
        after = _status(w.get_page(client))
        assert "0 recommendations" in after and "1 pending" not in after

    def test_every_review_entry_is_a_sym_card_like_the_recommender_cards(self, merged):
        entries = w.tab_panel(merged, "retirement").find_all("tenure-review-entry")
        assert entries
        assert all("sym-card" in e.classes() for e in entries)
        assert all(len(w.by_class(e, "card-name")) == 1 for e in entries)


# ===========================================================================
# The helpers
# ===========================================================================


class TestHelpers:
    def test_the_summary_tiles_helper_gives_five_labelled_values_for_a_good_run(self):
        import app as app_module

        s = w.CAPTURED_SUMMARY
        tiles = app_module._build_retirement_summary_tiles(s)
        assert [t["value"] for t in tiles] == [
            f"{s['symphonies_with_series']} of {s['roster_size']}",
            f"{s['series_days_min']} to {s['series_days_max']} days",
            f"{s['pairs_screened']} of {s['pairs_evaluated']}",
            str(
                s["blocked_uncertainty"]
                + s["blocked_stress_too_thin"]
                + s["blocked_stress_below_threshold"]
                + s["blocked_no_candidate"]
            ),
            str(s["recommendations"]),
        ]
        assert all(isinstance(t["label"], str) and t["label"] for t in tiles)
        assert str(s["screen_threshold"]) in tiles[2]["label"]

    def test_the_tiles_helper_reads_the_dash_for_each_missing_value_and_never_zero(self):
        import app as app_module

        for key, index in (
            ("symphonies_with_series", 0),
            ("series_days_min", 1),
            ("pairs_screened", 2),
            ("blocked_no_candidate", 3),
            ("recommendations", 4),
        ):
            tiles = app_module._build_retirement_summary_tiles(w.without_keys(key))
            assert tiles[index]["value"] == w.DASH, (key, tiles[index])

    def test_the_tiles_helper_does_not_change_its_input(self):
        import copy

        import app as app_module

        s = copy.deepcopy(w.CAPTURED_SUMMARY)
        app_module._build_retirement_summary_tiles(s)
        assert s == w.CAPTURED_SUMMARY

    def test_the_pointer_helper_for_each_state(self, monkeypatch):
        import app as app_module

        s = w.CAPTURED_SUMMARY
        assert "not run yet" in app_module._build_retirement_pointer(None, [])["status"]
        good = app_module._build_retirement_pointer(
            {**s, "recommendations": 2},
            [{"approval_status": "pending"}, {"approval_status": "approved"}],
        )["status"]
        assert "2 recommendations" in good and "1 pending" in good
        assert w.et_text(s["run_at"], _RUN_AT_FORMAT, -4) in good
        # Review retire candidates count with the cards: a candidate with no decision, or a
        # pending one, waits; an approved one, a rejected one and a non-candidate do not.
        review = [
            {"verdict": "retire_candidate", "approval_status": None},
            {"verdict": "retire_candidate", "approval_status": "pending"},
            {"verdict": "retire_candidate", "approval_status": "approved"},
            {"verdict": "retire_candidate", "approval_status": "rejected"},
            {"verdict": "keep", "approval_status": None},
        ]
        both = app_module._build_retirement_pointer(
            {**s, "recommendations": 2},
            [{"approval_status": "pending"}, {"approval_status": "approved"}],
            review,
        )["status"]
        assert "2 recommendations" in both and "3 pending" in both
        only_review = app_module._build_retirement_pointer({**s, "recommendations": 0}, [], review)[
            "status"
        ]
        assert "0 recommendations" in only_review and "2 pending" in only_review
        failed = app_module._build_retirement_pointer(w.internal_error_summary(monkeypatch), [])[
            "status"
        ]
        assert "internal error" in failed.lower()


class TestExclusionSentenceCallable:
    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            ("no_account", _SENTENCES["no_account"]),
            ("series_too_short", _SENTENCES["series_too_short"]),
            ("shadow_series_unavailable", _SENTENCES["shadow_series_unavailable"]),
            ("fetch_error: HTTP 429", "Its Composer series could not be fetched (HTTP 429)."),
            (
                "fetch_error: ConnectionError",
                "Its Composer series could not be fetched (ConnectionError).",
            ),
            ("something_new", "Excluded: something_new"),
            ("", "Reason not recorded."),
            (None, "Reason not recorded."),
            (7, "Reason not recorded."),
            (["no_account"], "Reason not recorded."),
        ],
    )
    def test_each_reason_reads_its_sentence(self, reason, expected):
        import app as app_module

        assert app_module._retirement_exclusion_reason_sentence(reason) == expected

    def test_the_resolver_stamps_reason_text_on_every_entry(self):
        import app as app_module

        last_run = {
            "symphonies_without_series": [
                {"symphony_id": "idA", "reason": "no_account"},
                {"symphony_id": "idB", "reason": "fetch_error: HTTP 503"},
                {"symphony_id": "idC", "reason": "brand_new_reason"},
                {"symphony_id": "idD", "reason": ""},
            ]
        }
        out = app_module._retirement_resolve_excluded_symphonies(last_run, lambda: {})
        assert [e["reason"] for e in out] == [
            "no_account",
            "fetch_error: HTTP 503",
            "brand_new_reason",
            "",
        ]
        assert [e["reason_text"] for e in out] == [
            app_module._retirement_exclusion_reason_sentence(e["reason"]) for e in out
        ]
        assert out[0]["reason_text"] == _SENTENCES["no_account"]

    def test_the_input_summary_is_not_changed_by_resolving(self):
        import copy

        import app as app_module

        last_run = {"symphonies_without_series": [{"symphony_id": "idA", "reason": "no_account"}]}
        snapshot = copy.deepcopy(last_run)
        app_module._retirement_resolve_excluded_symphonies(last_run, lambda: {})
        assert last_run == snapshot

    def test_the_reason_literals_are_read_from_the_recommender_not_redeclared(self):
        import ast
        import pathlib

        source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(
            encoding="utf-8"
        )
        literals = {"no_account", "series_too_short", "shadow_series_unavailable"}
        offenders = [
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (node.value in literals or node.value.startswith("fetch_error"))
        ]
        assert offenders == [], f"app.py declares exclusion reason literals: {offenders}"

    def test_no_script_or_template_carries_a_second_mapping(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        for rel in ("templates/ai_advisor.html", "static/ai_advisor.js"):
            text = (root / rel).read_text(encoding="utf-8")
            for literal in ("series_too_short", "no_account", "shadow_series_unavailable"):
                assert literal not in text, f"{rel} carries the reason literal {literal}"
