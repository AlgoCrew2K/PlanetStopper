"""The AI Advisor's descriptions, term definitions and reworded labels, read from the server render.

Subject: ``GET /ai-advisor`` through the Flask test client over seeded worlds (the real writers,
``tests/ui/_page_explainers.py``). The expected text of every row comes from
``tests/ui/_page_copy.py``; a slot is filled from the value this module patched into the slot's
authority, in the format the Values table states, so the expectation never just repeats what the
code printed.

Contract pinned (the plan's AC-1 to AC-4, AC-7 and AC-8 for the Advisor page):

* Every Copy table A row owned by the Advisor shows its exact text once, in the panel it names,
  as visible text in the element carrying ``data-explainer="<key>"`` (a kept row keeps its text
  and gains the attribute), directly under the heading it names.
* Each description that has Copy table B rows has ONE closed ``<details class="hp-more"
  data-terms="<key>">`` with the summary "What the terms mean" directly after it and one
  ``<p data-term="<row>">`` per row, in table order; every term is defined once on the page.
* A section that does not render carries no description and no disclosure (empty world, no key).
* The model sentences read their resolved form with the name, or their unresolved form, and the
  words "model unavailable" appear nowhere on the page.
* Chat, Frontrunner and Strategy Builder labels, the Frontrunner card title (name, " · proposal #",
  id) and the slots (tenure threshold, screen threshold, stress fraction, watch margin) with a
  whole-phrase fallback when an authority fails.
* Selectors that must not change: the tab dot titles, the cards' ``title`` and ``data-testid``.

Fixture provenance: ``tests/fixtures/page_explainers/copy.json`` (the approved plan's tables),
the captured retirement run row and the captured droplet state; the model names are marker
strings set through the operator override, which the page reads verbatim. The Strategy Builder
backtest-failure row is synthetic and labelled so in ``_page_explainers_advisor``.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

import database
from tests._ux_truth_support import captured_symphonies
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy_fixture
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui._shell_support import (  # noqa: F401  (the two overrides and the empty directory)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "ai_advisor.html"
SUGGESTION_MODEL = "marker-suggestion-model"
SYNTHESIS_MODEL = "marker-synthesis-model"
MARKUP_MODEL = '<img src=x onerror="alert(1)">'
TENURE_KEY = "TENURE_REVIEW_MIN_TRADING_DAYS"
LOCAL_BODY = "Hello"


class Opened:
    def __init__(self, html: str):
        self.html = html
        self.page = adv.soup(html)

    def text(self) -> str:
        return adv.norm(self.page.get_text(" "))


class Advisor:
    """Seeds a world, sets the environment, and returns the rendered Advisor page."""

    def __init__(self, tmp_path, monkeypatch):
        import app as app_module

        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.client = app_module.app.test_client()
        self.env_file = tmp_path / "page.env"

    def open(
        self,
        name: str = "full",
        *,
        key: bool = True,
        models: bool = False,
        env_text: str = "",
        seed=None,
        patches=None,
    ) -> Opened:
        mp = self.monkeypatch
        harness.credential_free_environment(
            self.env_file, mp.setattr, lambda n: mp.delenv(n, raising=False)
        )
        if env_text:
            self.env_file.write_text(env_text, encoding="utf-8")
        harness.seed_world(name, self.tmp_path, mp.setattr)
        if seed is not None:
            seed()
        if key:
            import advisors.asset_swap_engine as ase

            mp.setenv("ANTHROPIC_API_KEY", harness.PLACEHOLDER_KEY)
            mp.setattr(ase, "_has_composer_key", lambda: True)
        if models:
            mp.setenv("ADVISOR_SUGGESTION_MODEL", SUGGESTION_MODEL)
            mp.setenv("ADVISOR_SYNTHESIS_MODEL", SYNTHESIS_MODEL)
        if patches is not None:
            patches(mp)
        response = self.client.get("/ai-advisor")
        assert response.status_code == 200, f"GET /ai-advisor answered {response.status_code}"
        return Opened(response.get_data(as_text=True))


@pytest.fixture
def advisor(tmp_path, monkeypatch):
    return Advisor(tmp_path, monkeypatch)


def _seed_everything():
    adv.seed_strategy_builder(survivors=1, backtest_failures=1)
    adv.seed_frontrunner_proposals(2)


_full_world_values = adv.full_world_values
_expected_a = adv.expected_a


def test_every_advisor_description_is_this_plans_and_no_row_is_left_on_the_reference_text():
    """The Advisor's descriptions are all read from Copy table A of the plan; no key is left on the
    reference's text, so a test over the reference rows would silently cover nothing."""
    assert adv.OWNED_A_KEYS == (), adv.OWNED_A_KEYS
    for key in adv.OTHER_TAB_A_KEYS:
        assert copy_fixture.a_text(key), f"{key}: the plan gives it no description"
        assert copy_fixture.a_row(key)["slots"] == [], f"{key}: carries a slot"
    for key in adv.REMOVED_A_KEYS:
        assert copy_fixture.a_text(key) is None, f"{key}: the plan removes it"


# ---------------------------------------------------------------------------
# The other tabs' descriptions (S-3.1, S-3.3, S-3.6, S-3.7 for Correlations, Asset Swaps, Logic
# Changes, Chat, Strategy Builder and Frontrunner Builder)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", adv.OTHER_TAB_A_KEYS)
def test_a_description_shows_its_exact_text_once_in_its_panel(advisor, key):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    values = _full_world_values()
    nodes = adv.explainer_nodes(shown.page, key)
    assert len(nodes) == 1, f"{key}: {len(nodes)} elements carry data-explainer={key!r}"
    node = nodes[0]
    assert adv.norm(node.get_text(" ")) == adv.norm(_expected_a(key, values)), key
    assert adv.visible_in_markup(node), f"{key} is hidden by the markup"
    panel = adv.panel_of(shown.page, adv.TAB_OF_A_KEY[key])
    assert node in panel.select(f'[data-explainer="{key}"]'), f"{key} is not inside its panel"
    heading = adv.HEADING_OF_A_KEY.get(key)
    if heading is not None:
        before = adv.nearest_heading_before(panel, node)
        assert before.startswith(heading), (
            f"{key} sits under {before!r}, not directly under {heading!r}"
        )


def test_the_tab_description_is_visible_text_and_never_only_an_attribute(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    for key in adv.OTHER_TAB_A_KEYS:
        node = adv.explainer_nodes(shown.page, key)[0]
        assert adv.norm(node.get_text(" ")), f"{key} carries no text in the element"
        for attribute in ("title", "aria-label"):
            if node.has_attr(attribute):
                assert adv.norm(node[attribute]) != adv.norm(node.get_text(" ")), (
                    f"{key}: the text is repeated in {attribute} only as a stand-in"
                )


def test_a_tab_whose_dot_carries_a_title_keeps_the_title_and_names_its_meaning_in_view(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    assert adv.tab_dot_titles(shown.page) == adv.TAB_DOT_TITLES
    carried_by = {
        "overview": ("O03",),
        "correlations": ("C01",),
        "asset-swaps": ("W01",),
        "logic-changes": ("L01",),
        "chat": ("T02",),
        "strategy-builder": ("B00",),
        "frontrunner-builder": ("X01", "X03"),
    }
    banners = {
        "asset-swaps": "advise-only-banner",
        "logic-changes": "fdr-warning-banner",
        "strategy-builder": "strategy-builder-risk-banner",
        "frontrunner-builder": "frontrunner-builder-risk-banner",
    }
    for tab, keys in carried_by.items():
        panel = adv.panel_of(shown.page, tab)
        for key in keys:
            assert panel.select(f'[data-explainer="{key}"]'), f"{tab}: {key} is not on the tab"
        if tab in banners:
            banner = panel.select_one(f'[data-testid="{banners[tab]}"]')
            assert banner is not None and adv.visible_in_markup(banner), (
                f"{tab} lost its risk banner {banners[tab]}"
            )


def test_a_page_with_nothing_stored_describes_only_the_sections_it_renders(advisor):
    shown = advisor.open("empty", key=False)
    present = {k for k in adv.OTHER_TAB_A_KEYS if adv.explainer_nodes(shown.page, k)}
    assert "C01" in present, "C01: a rendered section lost its description"
    for key in adv.RETIREMENT_A_KEYS:
        assert adv.explainer_nodes(shown.page, key), (
            f"{key}: a rendered section lost its description"
        )
    for key in ("W01", "L01", "B00", "T02", "X01"):
        assert key in present, f"{key} is a tab line and always shows"
    for key in ("B07", "B10"):
        assert key not in present, f"{key} describes a section that does not render"
    # Their terms live in the tab's Glossary, which a page with no such section still holds.


def test_without_a_key_the_tab_lines_stay_and_the_try_panels_carry_no_orphaned_description(
    advisor,
):
    shown = advisor.open("full", key=False, seed=_seed_everything)
    for key in ("W01", "L01", "B00"):
        assert adv.explainer_nodes(shown.page, key), f"{key} is a tab line and must still show"
    for key in ("B03", "X03"):
        assert not adv.explainer_nodes(shown.page, key), (
            f"{key} describes controls that are replaced by the unavailable state"
        )
        assert not adv.disclosure_nodes(shown.page, key), f"{key}: an orphaned disclosure"
    for unavailable in shown.page.select(
        '[data-testid^="no-api-key-state"], [data-testid="chat-unavailable"]'
    ):
        assert not unavailable.select("[data-explainer]"), (
            "a description sits inside an unavailable state"
        )


# ---------------------------------------------------------------------------
# AC-4: the other tabs' Glossaries (S-4.1, S-4.2, S-4.3, S-4.5, S-4.7, S-4.11)
# ---------------------------------------------------------------------------


def _panel_glossary(shown: Opened, host: str):
    panel = adv.panel_of(shown.page, adv.TAB_OF_GLOSSARY_HOST[host])
    found = panel.select('details[data-terms="glossary"]')
    assert len(found) == 1, f"{host}: {len(found)} Glossaries in the panel, not one"
    return panel, found[0]


@pytest.mark.parametrize("host", adv.OTHER_TAB_GLOSSARY_HOSTS)
def test_a_tab_ends_with_one_closed_glossary_holding_table_gs_lines_in_order(advisor, host):
    """S-4.1 and S-4.2: one disclosure, closed, summary Glossary, the last element of the tab
    panel, one paragraph per Table G line in order with exactly the line's text."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    panel, glossary = _panel_glossary(shown, host)
    assert "hp-more" in glossary.get("class", []) and not glossary.has_attr("open")
    assert glossary.get("data-glossary-host") == host and glossary.get("data-testid") == "glossary"
    summary = glossary.find("summary", recursive=False)
    assert summary is not None and adv.norm(summary.get_text(" ")) == "Glossary"
    assert [c for c in panel.find_all(recursive=False)][-1] is glossary, (
        f"the Glossary is not the last element of the {host} panel"
    )
    lines = [c for c in glossary.find_all(recursive=False) if c.name != "summary"]
    expected = copy_fixture.glossary(host)
    assert [c.name for c in lines] == ["p"] * len(expected)
    assert [c.get("data-term") for c in lines] == [x["key"] for x in expected]
    for node, line in zip(lines, expected, strict=True):
        assert line["slots"] == [], f"{line['key']}: a slot the page has no value for"
        assert adv.norm(node.get_text(" ")) == adv.norm(line["text"]), line["key"]


@pytest.mark.parametrize("host", adv.OTHER_TAB_GLOSSARY_HOSTS)
def test_every_line_of_a_tab_glossary_is_unchanged_from_the_reference_row(advisor, host):
    """S-4.5 and S-4.7: no line of these six tabs is a Table G2 line, so each equals the reference
    row it moved from, word for word."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    _, glossary = _panel_glossary(shown, host)
    for line in copy_fixture.glossary(host):
        assert line["source"] == "ref", f"{line['key']}: premise, no G2 line on this tab"
        node = glossary.select_one(f'p[data-term="{line["key"]}"]')
        assert node is not None, line["key"]
        assert adv.norm(node.get_text(" ")) == adv.norm(copy_fixture.text(line["key"], "B")), line[
            "key"
        ]


@pytest.mark.parametrize("world", ["empty", "full"])
@pytest.mark.parametrize("host", adv.OTHER_TAB_GLOSSARY_HOSTS)
def test_a_tab_glossary_holds_every_line_whatever_sections_render(advisor, host, world):
    """S-4.11: with no run, no survivors, no proposals and no key, each Glossary still holds every
    line of its tab (the Strategy Builder's B07t and B12 describe a card that is not there)."""
    shown = advisor.open(world, key=False)
    _, glossary = _panel_glossary(shown, host)
    keys = [c.get("data-term") for c in glossary.find_all("p", recursive=False)]
    assert keys == [x["key"] for x in copy_fixture.glossary(host)]


@pytest.mark.parametrize("host", adv.OTHER_TAB_GLOSSARY_HOSTS)
def test_a_tab_carries_no_what_the_terms_mean_disclosure_and_defines_each_term_once(advisor, host):
    """S-4.3: no such summary, no data-terms but the Glossary, each line defined once in its tab."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    panel, _ = _panel_glossary(shown, host)
    summaries = [adv.norm(s.get_text(" ")) for s in panel.select("summary")]
    assert adv.DISCLOSURE_SUMMARY not in summaries, summaries
    others = [
        d["data-terms"] for d in panel.select("[data-terms]") if d["data-terms"] != "glossary"
    ]
    assert others == [], others
    for line in copy_fixture.glossary(host):
        found = panel.select(f'[data-term="{line["key"]}"]')
        assert len(found) == 1, f"{line['key']} is defined {len(found)} times in the {host} tab"


def test_no_advisor_tab_is_left_holding_a_what_the_terms_mean_disclosure(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    assert not shown.page.select('details[data-terms]:not([data-terms="glossary"])')
    assert adv.DISCLOSURE_SUMMARY not in [
        adv.norm(s.get_text(" ")) for s in shown.page.select("summary")
    ]
    assert len(shown.page.select('details[data-terms="glossary"]')) == len(
        adv.GLOSSARY_HOSTS_DONE
    ), "one Glossary per Advisor tab"


def test_every_term_is_defined_once_on_the_page(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    moved = adv.moved_term_keys()
    for record in (
        r
        for r in copy_fixture.rows("B")
        if r["host"] in adv.TAB_OF_B_HOST and r["key"] not in moved
    ):
        found = shown.page.select(f'[data-term="{record["key"]}"]')
        assert len(found) == 1, f"{record['key']} is defined {len(found)} times on the page"


def test_every_disclosure_is_closed_on_load_and_has_the_one_summary_everywhere(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    details = shown.page.select("details.hp-more")
    assert details, "the page holds no shared disclosure"
    for node in details:
        assert not node.has_attr("open")
        summary = adv.norm(node.find("summary").get_text(" "))
        # A host that still holds its terms in a disclosure keeps the reference summary; a host
        # whose terms moved holds one Glossary.
        assert summary in (adv.DISCLOSURE_SUMMARY, "Glossary"), summary
        assert (summary == "Glossary") == (node.get("data-terms") == "glossary"), summary


# ---------------------------------------------------------------------------
# AC-3: reworded labels (S-3.12, S-3.13, S-3.14, S-8.8 .. S-8.10)
# ---------------------------------------------------------------------------

MODEL_LINES = (
    ("MU2", "asset-swaps", "suggestion"),
    ("MU2", "logic-changes", "suggestion"),
    ("MU3", "chat", "synthesis"),
    ("MU4", "strategy-builder", "suggestion"),
)


def _attribution(shown: Opened, tab: str) -> str:
    node = adv.panel_of(shown.page, tab).select_one(".cap-tab-attribution")
    assert node is not None, f"{tab} has no model line"
    return adv.norm(node.get_text(" "))


@pytest.mark.parametrize(("row", "tab", "family"), MODEL_LINES)
def test_a_model_line_reads_its_resolved_form_with_the_name(advisor, row, tab, family):
    shown = advisor.open("full", key=True, models=True)
    name = SUGGESTION_MODEL if family == "suggestion" else SYNTHESIS_MODEL
    record = copy_fixture.row(row, "C")
    assert _attribution(shown, tab) == adv.norm(
        copy_fixture.fill(record["new_resolved"], model=name)
    )


@pytest.mark.parametrize(("row", "tab", "family"), MODEL_LINES)
def test_a_model_line_reads_its_unresolved_form_when_no_model_resolves(advisor, row, tab, family):
    shown = advisor.open("full", key=True, models=False)
    record = copy_fixture.row(row, "C")
    assert _attribution(shown, tab) == adv.norm(record["new_unresolved"])


@pytest.mark.parametrize("resolved", [True, False])
def test_the_market_prism_badge_reads_its_resolved_and_unresolved_forms(advisor, resolved):
    record = copy_fixture.row("MU1", "C")
    shown = advisor.open("full", key=True, models=resolved)
    badge = shown.page.select_one('[data-testid="prism-model-badge"]')
    assert badge is not None, "the Market Prism block has no model badge"
    wanted = (
        copy_fixture.fill(record["new_resolved"], model=SYNTHESIS_MODEL)
        if resolved
        else record["new_unresolved"]
    )
    assert adv.norm(badge.get_text(" ")) == adv.norm(wanted)


@pytest.mark.parametrize("resolved", [True, False])
def test_no_run_note_shows_under_propose_new_strategies_whatever_the_models_state(
    advisor, resolved
):
    """S-3.6 and CX-10: the note B03 is removed with its model sentence; the model line (MU4) and
    B00 say what it said."""
    shown = advisor.open("full", key=True, models=resolved, seed=_seed_everything)
    controls = shown.page.select_one('[data-testid="strategy-builder-controls"]')
    assert controls is not None, "premise: with a key the Strategy Builder shows its controls"
    assert not controls.select(".run-controls-note, [data-explainer]"), (
        "a note still sits in the run controls"
    )
    assert adv.explainer_nodes(shown.page, "B03") == []
    assert "The advisor generates candidate strategies" not in shown.text()
    assert "Only the single strongest candidate that clears the bar" not in shown.text()


def test_each_model_knob_is_read_once_so_a_badge_and_its_known_flag_come_from_one_choice(advisor):
    """A cache that changes between two reads of one knob must not split a name from its flag.

    The fake answers a resolved choice on a knob's first read and the unavailable one after it.
    Two reads would print "model not known" beside a name that was just resolved; one read shows
    the resolved wording, and the page asks once per knob.
    """
    import model_config

    marker = "marker-single-read-model"
    reads: dict = {}

    def flipping(knob):
        reads[knob] = reads.get(knob, 0) + 1
        if reads[knob] == 1:
            return model_config.ModelChoice(model_id=marker, source="override")
        return model_config.ModelChoice()

    shown = advisor.open(
        "full",
        key=True,
        patches=lambda mp: mp.setattr(model_config, "peek", flipping),
    )
    assert reads == {model_config.Knob.SUGGESTION: 1, model_config.Knob.SYNTHESIS: 1}, reads
    for row, tab, _family in MODEL_LINES:
        assert _attribution(shown, tab) == adv.norm(
            copy_fixture.fill(copy_fixture.row(row, "C")["new_resolved"], model=marker)
        ), f"{tab}: the model line split the name from its known flag"


def test_the_words_model_unavailable_appear_nowhere_on_a_page_with_no_model(advisor):
    shown = advisor.open("full", key=True, models=False, seed=_seed_everything)
    assert "model unavailable" not in shown.html.lower(), "the placeholder still prints"
    assert "model unavailable" not in shown.text().lower()


def test_the_words_model_unavailable_appear_nowhere_with_no_key_and_no_model(advisor):
    shown = advisor.open("empty", key=False, models=False)
    assert "model unavailable" not in shown.html.lower()


@pytest.mark.parametrize(
    ("row", "state"),
    [("T05", "chat"), ("X04", "frontrunner")],
)
def test_an_unavailable_body_names_where_the_key_goes(advisor, row, state):
    shown = advisor.open("full", key=False, seed=lambda: adv.seed_frontrunner_proposals(1))
    selector = (
        '[data-testid="chat-unavailable"] .chat-unavailable-body'
        if state == "chat"
        else '[data-testid="no-api-key-state-fr"] .no-api-key-body'
    )
    node = shown.page.select_one(selector)
    assert node is not None, f"no {selector} on a page with no key"
    assert adv.norm(node.get_text(" ")) == adv.norm(copy_fixture.text(row, "C"))
    assert "Set ANTHROPIC_API_KEY to enable explanations" not in shown.html


def test_the_old_words_of_the_reworded_labels_are_gone_from_the_page(advisor):
    shown = advisor.open("full", key=True, models=True, seed=_seed_everything)
    for gone in (
        "Advisory suite — operator must approve each change through safety gates",
        "reasons over your live tree",
        "View overlay delta",
        "Full spliced symphony (preview)",
        "community, statistically gated",
    ):
        if gone == "community, statistically gated":
            # MU4 keeps these words, but never after a plus sign.
            assert "+ community, statistically gated" not in shown.text()
        else:
            assert gone not in shown.text(), f"{gone!r} still shows"


def test_the_frontrunner_overlay_and_preview_summaries_read_their_new_words(advisor):
    def seed():
        adv.seed_frontrunner_proposals(2)
        database.insert_frontrunner_proposal(
            symphony_id="strategy-builder-retrofit-incumbent",
            proposal_source="strategy_builder_retrofit",
            candidate_tree=json.loads(
                adv._REAL_INCUMBENT_TREE.read_text(encoding="utf-8")  # noqa: SLF001
            ),
            metrics_json={"calmar": 1.1, "cagr": 0.1, "max_drawdown": -0.1},
        )

    shown = advisor.open("full", key=True, seed=seed)
    overlay = shown.page.select('[data-testid="fr-overlay-preview"] > summary')
    assert overlay, "no frontrunner card carries its overlay disclosure"
    assert {adv.norm(s.get_text(" ")) for s in overlay} == {copy_fixture.text("X14", "C")}
    raw = {
        adv.norm(s.get_text(" "))
        for s in shown.page.select('[data-testid="fr-raw-preview"] > summary')
    }
    assert copy_fixture.text("X15", "C") in raw
    assert "Proposed symphony (preview)" in raw, "the retrofit card keeps its own label"
    assert not [t for t in raw if "spliced" in t.lower()]


def _incumbent_name() -> str:
    names = dict(captured_symphonies())
    return names[adv._FR_INCUMBENT_HASH]  # noqa: SLF001


def test_five_proposals_for_one_incumbent_have_five_different_titles(advisor):
    ids: list[int] = []

    def seed():
        ids.extend(adv.seed_frontrunner_proposals(5))

    shown = advisor.open("full", key=True, seed=seed)
    titles = [
        adv.norm(n.get_text(" ")) for n in shown.page.select('[data-testid="fr-incumbent-name"]')
    ]
    record = copy_fixture.row("NM8", "C")
    wanted = {
        adv.norm(copy_fixture.fill(record["new_text"], name=_incumbent_name(), id=str(i)))
        for i in ids
    }
    assert len(ids) == 5 and len(set(ids)) == 5, "premise: five different proposal ids"
    assert wanted <= set(titles), (
        f"missing titles {sorted(wanted - set(titles))}; page has {titles}"
    )
    assert len({t for t in titles if t in wanted}) == 5, "two cards share a title"


def test_a_title_with_no_resolved_name_reads_the_hash_then_the_proposal_id(advisor):
    ids: list[int] = []
    unknown = "page-explainers-unknown-incumbent-hash"

    def seed():
        ids.extend(adv.seed_frontrunner_proposals(1, incumbent=unknown))

    shown = advisor.open("empty", key=True, seed=seed)
    record = copy_fixture.row("NM8", "C")
    titles = [
        adv.norm(n.get_text(" ")) for n in shown.page.select('[data-testid="fr-incumbent-name"]')
    ]
    assert titles == [adv.norm(copy_fixture.fill(record["new_text"], name=unknown, id=str(ids[0])))]


def test_a_cards_hash_title_testids_and_controls_are_unchanged(advisor):
    ids: list[int] = []
    shown = advisor.open(
        "full", key=True, seed=lambda: ids.extend(adv.seed_frontrunner_proposals(2))
    )
    cards = shown.page.select('[data-testid="frontrunner-proposal-card"]')
    assert len(cards) >= 2
    for card in cards:
        name = card.select_one('[data-testid="fr-incumbent-name"]')
        assert name["title"] == adv._FR_INCUMBENT_HASH  # noqa: SLF001
        assert card.select_one('[data-testid="fr-approve-btn"]') is not None
        assert card.select_one('[data-testid="stats-table"]') is not None
        for testid in ("fr-candidate-calmar", "fr-candidate-cagr", "fr-candidate-mdd"):
            assert card.select_one(f'[data-testid="{testid}"]') is not None, testid
    first = next(c for c in cards if c["id"] == f"fr-card-{ids[0]}")
    assert adv.norm(first.select_one('[data-testid="fr-candidate-calmar"]').get_text()) == (
        f"{adv._FR_METRICS['candidate_calmar']:.3f}"  # noqa: SLF001
    )
    assert first.select_one('[data-testid="fr-approve-btn"]')["onclick"] == f"frApprove({ids[0]})"


# ---------------------------------------------------------------------------
# AC-4: configured values (S-4.1 .. S-4.6, S-4.9, S-4.11)
# ---------------------------------------------------------------------------


def _r22_text(shown: Opened) -> str:
    return adv.norm(adv.explainer_nodes(shown.page, "R22")[0].get_text(" "))


def _r02_text(shown: Opened) -> str:
    return adv.norm(adv.explainer_nodes(shown.page, "R02")[0].get_text(" "))


def _term_text(shown: Opened, key: str) -> str:
    return adv.norm(shown.page.select_one(f'[data-term="{key}"]').get_text(" "))


def _tenure_row(value: str) -> str:
    phrase, _ = adv.phrase_of("tenure_threshold", "R22")
    return adv.norm(copy_fixture.fill(phrase, tenure_threshold=value))


def test_the_tenure_threshold_is_the_env_file_value_over_the_process_environment(
    advisor, monkeypatch
):
    monkeypatch.setenv(TENURE_KEY, "120")
    shown = advisor.open("dashboard_full", env_text=f"{TENURE_KEY}=90\n")
    assert _tenure_row("90") in _r22_text(shown)
    assert _tenure_row("120") not in _r22_text(shown)


def test_the_tenure_threshold_is_the_stored_reviews_value_over_the_environment(
    advisor, monkeypatch
):
    monkeypatch.setenv(TENURE_KEY, "90")
    stored = str(pb.full_record()["threshold_in_effect"])
    assert stored != "90", "premise: the stored review and the environment differ"
    shown = advisor.open("full", env_text=f"{TENURE_KEY}=90\n")
    assert _tenure_row(stored) in _r22_text(shown)
    assert _tenure_row("90") not in _r22_text(shown)


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "-5", "12.5"])
def test_the_tenure_threshold_is_what_the_next_review_would_use_for_each_input(advisor, raw):
    from advisors import tenure_review

    resolved = str(tenure_review.parse_threshold(raw).value)
    env_text = "" if raw is None else f"{TENURE_KEY}={raw}\n"
    shown = advisor.open("dashboard_full", env_text=env_text)
    assert _tenure_row(resolved) in _r22_text(shown), (raw, resolved)


def test_a_failing_tenure_authority_replaces_the_whole_phrase(advisor):
    def boom(raw):
        raise RuntimeError("authority failed")

    shown = advisor.open(
        "dashboard_full",
        patches=lambda mp: mp.setattr("advisors.tenure_review.parse_threshold", boom),
    )
    text = _r22_text(shown)
    phrase, fallback = adv.phrase_of("tenure_threshold", "R22")
    assert adv.norm(fallback) in text
    assert "{" not in text and not re.search(r"\binvested for \d", text)


def test_the_screen_threshold_follows_the_constant_when_no_run_is_stored(advisor):
    shown = advisor.open(
        "dashboard_full",
        patches=lambda mp: mp.setattr(
            "advisors.retirement_recommender.CORRELATION_SCREEN_THRESHOLD", 0.7
        ),
    )
    phrase, _ = adv.phrase_of("screen_threshold", "R02")
    assert adv.norm(copy_fixture.fill(phrase, screen_threshold="0.7")) in _r02_text(shown)


def test_the_screen_threshold_is_the_stored_runs_value_even_when_the_constant_moves(advisor):
    stored = str(json.loads(w.CAPTURE_ROW["raw_response"])["screen_threshold"])
    assert stored != "0.9", "premise: the patched constant differs from the stored value"
    shown = advisor.open(
        "full",
        patches=lambda mp: mp.setattr(
            "advisors.retirement_recommender.CORRELATION_SCREEN_THRESHOLD", 0.9
        ),
    )
    phrase, _ = adv.phrase_of("screen_threshold", "R02")
    text = _r02_text(shown)
    assert adv.norm(copy_fixture.fill(phrase, screen_threshold=stored)) in text
    assert adv.norm(copy_fixture.fill(phrase, screen_threshold="0.9")) not in text


@pytest.mark.parametrize("fraction, printed", [(0.1, "10"), (0.125, "12.5"), (0.05, "5")])
def test_the_stress_fraction_is_printed_as_a_whole_number_or_one_decimal(
    advisor, fraction, printed
):
    shown = advisor.open(
        "dashboard_full",
        patches=lambda mp: mp.setattr(
            "advisors.retirement_recommender.STRESS_WINDOW_FRACTION", fraction
        ),
    )
    phrase, _ = adv.phrase_of("stress_fraction_pct", "R19b")
    assert adv.norm(copy_fixture.fill(phrase, stress_fraction_pct=printed)) in _term_text(
        shown, "R19b"
    )


@pytest.mark.parametrize("margin, printed", [(7.5, "7.5"), (7.0, "7"), (12.25, "12.25")])
def test_the_watch_margin_has_no_trailing_zero(advisor, margin, printed):
    shown = advisor.open(
        "dashboard_full",
        patches=lambda mp: mp.setattr("advisors.tenure_review.BACKTEST_TRAIL_WATCH_PP", margin),
    )
    phrase, _ = adv.phrase_of("watch_trail_pp", "R27b")
    assert adv.norm(copy_fixture.fill(phrase, watch_trail_pp=printed)) in _term_text(shown, "R27b")


SLOT_AUTHORITIES = [
    (
        "screen_threshold",
        "R02",
        "A",
        "advisors.retirement_recommender",
        "CORRELATION_SCREEN_THRESHOLD",
    ),
    (
        "stress_fraction_pct",
        "R19b",
        "B",
        "advisors.retirement_recommender",
        "STRESS_WINDOW_FRACTION",
    ),
    ("watch_trail_pp", "R27b", "B", "advisors.tenure_review", "BACKTEST_TRAIL_WATCH_PP"),
]


@pytest.mark.parametrize(("slot", "row", "table", "module", "name"), SLOT_AUTHORITIES)
@pytest.mark.parametrize("failure", ["absent", "none", "nan", "bool"])
def test_an_authority_that_fails_replaces_the_whole_phrase_with_its_fallback(
    advisor, slot, row, table, module, name, failure
):
    def patches(mp):
        if failure == "absent":
            mp.delattr(module + "." + name)
        else:
            mp.setattr(
                module + "." + name, {"none": None, "nan": float("nan"), "bool": True}[failure]
            )

    shown = advisor.open("dashboard_full", patches=patches)
    text = _r02_text(shown) if table == "A" else _term_text(shown, row)
    phrase, fallback = adv.phrase_of(slot, row)
    assert adv.norm(fallback) in text, f"{row}: the fallback phrase is missing: {text!r}"
    assert "{" not in text and "None" not in text and "nan" not in text.lower().split()


def test_one_failing_authority_leaves_the_other_slots_unchanged(advisor):
    values = _full_world_values()

    def patches(mp):
        mp.delattr("advisors.retirement_recommender.STRESS_WINDOW_FRACTION")

    shown = advisor.open("full", seed=_seed_everything, patches=patches)
    assert adv.norm(adv.expected_row("R22", "A", **values)) == _r22_text(shown)
    assert adv.norm(adv.expected_row("R02", "A", **values)) == _r02_text(shown)
    watch = adv.expected_row("R27b", "B", **values)
    assert _term_text(shown, "R27b") == adv.norm(watch)


def test_no_slot_value_is_typed_into_the_template_next_to_the_copy():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert 'data-explainer="R22"' in source, "the tenure description is not in the template"
    for slot_row in copy_fixture.slot_rows():
        if slot_row["copy_row"] not in {"R22", "R02", "R19b", "R27b"}:
            continue
        for literal in re.findall(r"\{(\w+)\}", slot_row["phrase"]):
            assert "{" + literal + "}" not in source, (
                "a slot placeholder is left as literal text in the template"
            )
    for number in ("126", "0.65", "0.9", "7.5"):
        assert not re.search(
            rf"(invested for {re.escape(number)} trading days|correlation of {re.escape(number)} or more)",
            source,
        ), f"the value {number} is typed beside the copy"


# ---------------------------------------------------------------------------
# AC-7: the tab strip cue is built from existing tokens and shapes (S-7.10)
# ---------------------------------------------------------------------------


def _cue_rules(css: str) -> list[tuple[str, str]]:
    return [
        (m.group(1).strip(), m.group(2))
        for m in re.finditer(r"([^{}]*cap-nav-cue[^{}]*)\{([^{}]*)\}", css)
    ]


def test_the_tab_strip_cue_uses_only_existing_tokens_and_shapes():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert 'data-testid="cap-nav-cue-start"' in source and 'data-testid="cap-nav-cue-end"' in source
    for testid in ("cap-nav-cue-start", "cap-nav-cue-end"):
        tag = re.search(rf'<[^>]*data-testid="{testid}"[^>]*>', source)
        assert tag and 'aria-hidden="true"' in tag.group(0), f"{testid} is not aria-hidden"
    rules = _cue_rules(source)
    assert rules, "the cue has no style rule in the template"
    for selector, body in rules:
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", body), (
            f"{selector}: a raw colour literal"
        )
        assert "url(" not in body and "content:" not in body, f"{selector}: an icon or image"
        assert "font-size" not in body or "var(" in body, f"{selector}: a new type size"
    joined = " ".join(body for _, body in rules)
    assert "var(--studio-paper)" in joined and "transparent" in joined, (
        "the cue is an edge fade from the paper token to transparent"
    )


# ---------------------------------------------------------------------------
# describe('security')
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_a_model_name_carrying_markup_is_escaped_in_every_model_sentence(
        self, advisor, monkeypatch
    ):
        shown = advisor.open(
            "full",
            key=True,
            patches=lambda mp: (
                mp.setenv("ADVISOR_SUGGESTION_MODEL", MARKUP_MODEL),
                mp.setenv("ADVISOR_SYNTHESIS_MODEL", MARKUP_MODEL),
            ),
            seed=_seed_everything,
        )
        assert MARKUP_MODEL not in shown.html, "a model name was written as live markup"
        assert "&lt;img" in shown.html, "the escaped model name is not on the page"
        assert not shown.page.select("img[onerror]")

    def test_an_env_value_carrying_markup_never_reaches_a_description(self, advisor):
        shown = advisor.open(
            "dashboard_full", env_text=f"{TENURE_KEY}=<b>9</b><script>1</script>\n"
        )
        assert "<script>1</script>" not in shown.html
        node = adv.explainer_nodes(shown.page, "R22")[0]
        assert not node.find(["script", "b"])

    def test_no_description_or_disclosure_is_written_as_markup_the_template_marks_safe(self):
        source = TEMPLATE.read_text(encoding="utf-8")
        matches = list(re.finditer(r'data-(explainer|terms|term)="[^"]*"', source))
        assert any(m.group(1) == "explainer" for m in matches), (
            "the template carries no description"
        )
        # Every term line is a Glossary line (the macro writes the one data-terms).
        assert any(m.group(1) == "term" for m in matches), "the template carries no term line"
        for match in matches:
            window = source[match.start() : match.start() + 600]
            assert "| safe" not in window and "|safe" not in window, match.group(0)

    def test_a_description_carries_no_script_or_event_handler(self, advisor):
        shown = advisor.open("full", key=True, seed=_seed_everything)
        nodes = shown.page.select("[data-explainer], details.hp-more")
        assert len(nodes) >= len(adv.PLAN_A_KEYS), "the page carries no descriptions to scan"
        for node in nodes:
            assert not node.find("script")
            for el in [node, *node.find_all(True)]:
                assert not [a for a in el.attrs if a.lower().startswith("on")], el.name

    def test_a_proposal_whose_incumbent_name_is_markup_is_escaped_in_its_title(self, advisor):
        import database as db

        hostile = "<script>alert(1)</script>"

        def seed():
            state = {"hostile-hash": {"name": hostile}}
            existing = db.load_state() or {}
            existing.update(state)
            db.save_state(existing)
            adv.seed_frontrunner_proposals(1, incumbent="hostile-hash")

        shown = advisor.open("empty", key=True, seed=seed)
        assert hostile not in shown.html
        assert "&lt;script&gt;" in shown.html


# ---------------------------------------------------------------------------
# Every section description is one short sentence, with a slot filled or its fallback in place
# ---------------------------------------------------------------------------

DESCRIPTION_MAX_WORDS = 25
DESCRIPTION_SENTENCE_BREAK = re.compile(r"(?<=[.?])\s+(?=[A-Z0-9\"'(])")
DESCRIPTION_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⭐⭕️]")
DESCRIPTION_DASHES = ("—", "–")
# The longest a slot's authority prints: six decimals for a threshold, four digits for a day count.
LONGEST_SLOT_VALUE = {"screen_threshold": "0.123456", "tenure_threshold": "1260"}


def description_problems(text: str) -> list[str]:
    """What is wrong with a description: not one sentence, over 25 words, or a banned character."""
    problems = []
    sentences = [s for s in DESCRIPTION_SENTENCE_BREAK.split(text.strip()) if s]
    if len(sentences) != 1:
        problems.append(f"{len(sentences)} sentences")
    if len(text.split()) > DESCRIPTION_MAX_WORDS:
        problems.append(f"{len(text.split())} words")
    if "!" in text:
        problems.append("an exclamation mark")
    if any(dash in text for dash in DESCRIPTION_DASHES):
        problems.append("a dash")
    if DESCRIPTION_EMOJI.search(text):
        problems.append("an emoji")
    if re.search(r"\{[a-z_]+\}", text):
        problems.append("an unfilled slot")
    return problems


def filled_description(row: dict, mode: str) -> str:
    """The row's text with each slot phrase filled by its longest authority value (``value``) or
    replaced whole by its fallback phrase (``fallback``)."""
    text = copy_fixture.a_text(row["key"])
    for slot_row in copy_fixture.new_slot_rows():
        if slot_row["copy_row"] != row["key"]:
            continue
        if mode == "value":
            replacement = copy_fixture.fill(
                slot_row["phrase"], **{slot_row["slot"]: LONGEST_SLOT_VALUE[slot_row["slot"]]}
            )
        else:
            replacement = slot_row["fallback"]
        assert slot_row["phrase"] in text, (
            f"{row['key']}: the phrase for {slot_row['slot']} is gone"
        )
        text = text.replace(slot_row["phrase"], replacement)
    return text


DESCRIBED_ROWS = [r for r in copy_fixture.a_rows() if copy_fixture.a_text(r["key"]) is not None]


@pytest.mark.parametrize("mode", ["value", "fallback"])
@pytest.mark.parametrize("row", DESCRIBED_ROWS, ids=[r["key"] for r in DESCRIBED_ROWS])
def test_every_section_description_is_one_sentence_of_at_most_25_words(row, mode):
    problems = description_problems(filled_description(row, mode))
    assert not problems, f"{row['key']} ({mode}): {problems}"


def test_the_descriptions_cover_every_host_and_a_removed_row_reads_none():
    hosts = {copy_fixture.host_of(r) for r in DESCRIBED_ROWS}
    assert len(DESCRIBED_ROWS) >= 45 and {"/", "Performance", "History", "Settings"} <= hosts, hosts
    removed = [r for r in copy_fixture.a_rows() if r["kind"] in ("removed", "moved")]
    assert removed and all(copy_fixture.a_text(r["key"]) is None for r in removed)
    slotted = {
        s["copy_row"]
        for s in copy_fixture.new_slot_rows()
        if s["copy_row"] in {r["key"] for r in DESCRIBED_ROWS}
    }
    assert {"R02", "R22"} <= slotted, "the rows with a slot are checked with it filled"


def test_the_description_check_reports_each_fault_it_checks_for():
    assert description_problems("One short sentence.") == []
    assert any("words" in p for p in description_problems(" ".join(["word"] * 26) + "."))
    assert description_problems(" ".join(["word"] * 25) + ".") == []
    assert any("sentences" in p for p in description_problems("First one. Second one."))
    assert any("exclamation" in p for p in description_problems("Nice!"))
    assert any("dash" in p for p in description_problems("A — B."))
    assert any("dash" in p for p in description_problems("A – B."))
    assert any("emoji" in p for p in description_problems("A \U0001f680 launch."))
    assert any("slot" in p for p in description_problems("At {screen_threshold} or more."))


# ---------------------------------------------------------------------------
# The navigation panel and the Overview: descriptions, other texts and the Glossary
# (the plan's AC-2, AC-3 and AC-4 for the part of the Advisor page above the other tabs)
#
# The expected text of every row is this plan's (``a_text``, ``x_row``, ``glossary``); the
# reference rows these replace are still read for what must be gone.
# ---------------------------------------------------------------------------

OLD_PLACEHOLDER = "Select a symphony and click Run Claude advisor to get suggestions."
OLD_GUARDRAIL_TAIL = "Per-symphony recommendations come from the Run Claude advisor button."


def _overview(shown: Opened):
    return adv.panel_of(shown.page, "overview")


def _position(order, node) -> int:
    return next(i for i, n in enumerate(order) if n is node)


def _glossary_of(panel):
    found = panel.select('details[data-terms="glossary"]')
    assert len(found) == 1, f"{len(found)} Glossaries in the panel, not one"
    return found[0]


def _without_glossaries(page) -> str:
    """The page's text with every Glossary, script and style removed: the section bodies."""
    clone = adv.soup(str(page))
    for dead in clone(["script", "style", "template"]):
        dead.decompose()
    for glossary in clone.select('details[data-terms="glossary"]'):
        glossary.decompose()
    return adv.norm(clone.get_text(" "))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+", adv.norm(text)) if s]


def test_the_navigation_panel_holds_only_the_tab_bar(advisor):
    """S-3.5 and the markup half of S-2.1: no description, no disclosure, no link and no text
    beside or above the tab bar."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    nav = shown.page.select_one('[data-testid="advisor-nav-panel"]')
    assert nav is not None, "the page has no navigation panel"
    assert not nav.select("[data-explainer]"), "the navigation panel carries a description"
    assert not nav.select("details, [data-terms], [data-term], p"), (
        "the navigation panel carries a disclosure or a paragraph"
    )
    children = [c for c in nav.find_all(recursive=False)]
    assert [c.name for c in children] == ["div"] and "cap-nav-wrap" in children[0].get("class", [])
    assert adv.explainer_nodes(shown.page, "A01") == [], "the caption A01 is still on the page"
    assert not shown.page.select('[data-testid="advisor-caption"]')
    own_text = adv.norm(" ".join(nav.stripped_strings))
    assert "Advisory tools" not in own_text and "Tab dots" not in own_text


@pytest.mark.parametrize("key", adv.OVERVIEW_A_KEYS)
def test_an_overview_description_shows_its_new_text_once_under_its_heading(advisor, key):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    nodes = adv.explainer_nodes(shown.page, key)
    assert len(nodes) == 1, f"{key}: {len(nodes)} elements carry data-explainer={key!r}"
    node = nodes[0]
    assert adv.norm(node.get_text(" ")) == adv.norm(copy_fixture.a_text(key)), key
    assert adv.visible_in_markup(node), f"{key} is hidden by the markup"
    panel = _overview(shown)
    assert node in panel.select(f'[data-explainer="{key}"]'), f"{key} is not inside its panel"
    heading = adv.HEADING_OF_A_KEY.get(key)
    if heading is not None:
        before = adv.nearest_heading_before(panel, node)
        assert before.startswith(heading), (
            f"{key} sits under {before!r}, not directly under {heading!r}"
        )


def test_the_overview_has_no_tab_line_and_its_toolbar_sentence_sits_above_the_toolbar(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    assert adv.explainer_nodes(shown.page, "O01") == [], "the tab-level line O01 is still shown"
    panel = _overview(shown)
    order = [n for n in panel.descendants if getattr(n, "name", None)]
    sentence = adv.explainer_nodes(shown.page, "O03")[0]
    toolbar = panel.select_one('[data-testid="advisor-controls"]')
    assert _position(order, sentence) < _position(order, toolbar)


@pytest.mark.parametrize("world", ["empty", "full"])
def test_the_overview_descriptions_show_with_nothing_stored_too(advisor, world):
    shown = advisor.open(world, key=False)
    for key in adv.OVERVIEW_A_KEYS:
        assert len(adv.explainer_nodes(shown.page, key)) == 1, f"{key} is not shown in {world}"
    for key in adv.REMOVED_A_KEYS:
        assert adv.explainer_nodes(shown.page, key) == [], f"{key} is shown in {world}"


def test_the_suggestions_placeholder_reads_no_suggestions_yet(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    placeholder = shown.page.select_one('[data-testid="suggestions-placeholder"]')
    assert placeholder is not None
    assert adv.norm(placeholder.get_text(" ")) == copy_fixture.x_row("CX-1")["new_text"]
    assert copy_fixture.x_row("CX-1")["new_text"] == "No suggestions yet."


def test_the_guardrail_paragraph_and_the_pointer_class_are_gone_from_the_page(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    assert not shown.page.select('[data-testid="guardrail-uniform-note"]')
    assert copy_fixture.x_row("CX-3")["kind"] == "removed"
    assert not shown.page.select(".adv-pointer, .obs-framing-note")


def test_every_old_text_the_overview_dropped_is_in_no_section_body(advisor):
    """S-3.3 for the Overview and the navigation panel: the reference text of each removed or
    shortened row, and each old sentence the new text drops, appears in no section body, hidden
    panels included. A sentence a Glossary line took over lives in the Glossary only."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    body = _without_glossaries(shown.page)
    problems = []
    for key in ("A01", "O01", "O03", "O08", "O15", "O27"):
        old = adv.norm(copy_fixture.text(key, "A"))
        new = copy_fixture.a_text(key)
        kept = set(_sentences(new)) if new else set()
        if old in body:
            problems.append(f"{key}: the whole old text is on the page")
        for sentence in _sentences(old):
            if sentence not in kept and sentence in body:
                problems.append(f"{key}: the old sentence {sentence!r} is on the page")
    for literal in (OLD_PLACEHOLDER, OLD_GUARDRAIL_TAIL):
        if literal in body:
            problems.append(f"the old text {literal!r} is on the page")
    assert not problems, "\n".join(problems)


def test_the_old_guardrail_sentences_are_in_the_glossary_and_nowhere_else(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    glossary = adv.norm(_glossary_of(_overview(shown)).get_text(" "))
    body = _without_glossaries(shown.page)
    for key in ("O31a", "O31b", "O31c"):
        line = next(x for x in copy_fixture.glossary("Overview") if x["key"] == key)
        sentence = adv.norm(line["text"]).split(": ", 1)[1]
        assert sentence in glossary, f"{key}: the sentence is not in the Glossary"
        assert sentence not in body, f"{key}: the sentence is also in a section body"


def test_the_overview_ends_with_one_closed_glossary_holding_table_gs_lines_in_order(advisor):
    """S-4.1 and S-4.2 for the Overview: one disclosure, closed, summary Glossary, the last
    element of the tab panel, one paragraph per line in order with exactly the line's text."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    panel = _overview(shown)
    glossary = _glossary_of(panel)
    assert "hp-more" in glossary.get("class", []), "the Glossary is not the shared closed look"
    assert not glossary.has_attr("open"), "the Glossary is open on load"
    assert glossary.has_attr("data-glossary-host") and glossary.get("data-testid") == "glossary"
    summary = glossary.find("summary", recursive=False)
    assert summary is not None and adv.norm(summary.get_text(" ")) == "Glossary"
    last = [c for c in panel.find_all(recursive=False)][-1]
    assert last is glossary, "the Glossary is not the last element of the Overview panel"
    lines = [c for c in glossary.find_all(recursive=False) if c.name != "summary"]
    expected = copy_fixture.glossary("Overview")
    assert [c.name for c in lines] == ["p"] * len(expected)
    assert [c.get("data-term") for c in lines] == [line["key"] for line in expected]
    for node, line in zip(lines, expected, strict=True):
        assert adv.norm(node.get_text(" ")) == adv.norm(line["text"]), line["key"]


def test_the_glossary_changed_and_new_lines_read_their_g2_text(advisor):
    """S-4.5 for the Overview: O31a, O31b and O31c are the lines Table G2 writes."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    glossary = _glossary_of(_overview(shown))
    g2 = [line for line in copy_fixture.glossary("Overview") if line["source"] == "G2"]
    assert [line["key"] for line in g2] == ["O31a", "O31b", "O31c"]
    for line in g2:
        found = glossary.select(f'p[data-term="{line["key"]}"]')
        assert len(found) == 1 and adv.norm(found[0].get_text(" ")) == adv.norm(line["text"])


def test_an_unchanged_overview_line_equals_its_reference_row(advisor):
    """S-4.7 for the Overview: a line that is not in Table G2 reads the reference fixture's text."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    glossary = _glossary_of(_overview(shown))
    unchanged = [x for x in copy_fixture.glossary("Overview") if x["source"] == "ref"]
    assert len(unchanged) >= 15, "premise: most Overview lines are unchanged"
    for line in unchanged:
        node = glossary.select_one(f'p[data-term="{line["key"]}"]')
        assert node is not None, line["key"]
        assert adv.norm(node.get_text(" ")) == adv.norm(copy_fixture.text(line["key"], "B")), line[
            "key"
        ]


@pytest.mark.parametrize("world", ["empty", "full"])
def test_the_glossary_holds_every_line_whatever_sections_render(advisor, world):
    """S-4.11: with no run, no Prism row and no observations the Glossary still holds each line."""
    shown = advisor.open(world, key=False)
    glossary = _glossary_of(_overview(shown))
    keys = [c.get("data-term") for c in glossary.find_all("p", recursive=False)]
    assert keys == [line["key"] for line in copy_fixture.glossary("Overview")]


def test_the_navigation_panel_and_the_overview_carry_no_what_the_terms_mean_disclosure(advisor):
    """S-4.3 for the part C2 owns: no such summary, and no data-terms but the Glossary."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    nav = shown.page.select_one('[data-testid="advisor-nav-panel"]')
    for scope in (nav, _overview(shown)):
        summaries = [adv.norm(s.get_text(" ")) for s in scope.select("summary")]
        assert adv.DISCLOSURE_SUMMARY not in summaries, summaries
        others = [d for d in scope.select("[data-terms]") if d["data-terms"] != "glossary"]
        assert others == [], [d["data-terms"] for d in others]
    for line in copy_fixture.glossary("Overview"):
        found = shown.page.select(f'#tab-panel-overview [data-term="{line["key"]}"]')
        assert len(found) == 1, f"{line['key']} is defined {len(found)} times in the Overview"


def test_the_page_loads_the_glossary_script_once_beside_its_own_scripts(advisor):
    """The titles come from static/glossary.js, loaded once by the page that holds a Glossary."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    sources = [s["src"].split("?")[0] for s in shown.page.select("script[src]")]
    assert sources.count("/static/glossary.js") == 1, sources
    assert "/static/ai_advisor.js" in sources


def test_the_sentiment_chip_and_the_verification_header_name_their_glossary_lines(advisor):
    """TG3-7 in the server render: the chip carries data-glossary O16, the header O24."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    chip = shown.page.select_one('[data-testid="prism-sentiment-chip"]')
    assert chip is not None, "premise: a stored read renders its chip"
    assert chip.get("data-glossary") == "O16"
    glossary = _glossary_of(_overview(shown))
    assert glossary.select_one('p[data-term="O16"]') is not None


def test_the_numeric_verification_header_names_its_glossary_line(advisor):
    from tests._advisor_overview_prism import prism_raw_response, store_prism_row

    def seed():
        store_prism_row()
        database.insert_advisor_observation(
            advisor_role="MARKET_PRISM_VERIFICATION",
            subject_type="portfolio",
            subject_id="global",
            verdict=None,
            raw_response={
                "run_id": prism_raw_response()["run_id"],
                "checks": [],
                "summary": {"n_checks": 0},
                "verdict": "no-numeric-claims",
            },
        )

    shown = advisor.open("full", key=True, seed=seed)
    header = shown.page.select_one(".prism-verification-header")
    if header is None:
        pytest.fail("premise: the stored verification row renders its header")
    assert header.get("data-glossary") == "O24"


# ---------------------------------------------------------------------------
# The Retirement tab: descriptions, other texts and the Glossary
# (the plan's AC-3 and AC-4 for the tab; its tiles and evidence headings carry their lines as titles)
# ---------------------------------------------------------------------------

OLD_R12 = (
    "Run recommender: runs the nightly analysis now; it takes a minute or two and is refused "
    "while the market is open."
)


def _retirement(shown: Opened):
    return adv.panel_of(shown.page, "retirement")


@pytest.mark.parametrize("key", adv.RETIREMENT_A_KEYS)
def test_a_retirement_description_shows_its_new_text_once_under_its_heading(advisor, key):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    values = _full_world_values()
    nodes = adv.explainer_nodes(shown.page, key)
    assert len(nodes) == 1, f"{key}: {len(nodes)} elements carry data-explainer={key!r}"
    node = nodes[0]
    assert adv.norm(node.get_text(" ")) == adv.norm(adv.expected_a(key, values)), key
    assert adv.visible_in_markup(node), f"{key} is hidden by the markup"
    panel = _retirement(shown)
    assert node in panel.select(f'[data-explainer="{key}"]'), f"{key} is not inside its panel"
    before = adv.nearest_heading_before(panel, node)
    assert before.startswith(adv.HEADING_OF_A_KEY[key]), (
        f"{key} sits under {before!r}, not directly under {adv.HEADING_OF_A_KEY[key]!r}"
    )


@pytest.mark.parametrize("world", ["empty", "full"])
def test_the_retirement_descriptions_show_with_nothing_stored_too(advisor, world):
    shown = advisor.open(world, key=False)
    for key in adv.RETIREMENT_A_KEYS:
        assert len(adv.explainer_nodes(shown.page, key)) == 1, f"{key} is not shown in {world}"


def test_each_retirement_description_is_directly_followed_by_what_the_section_holds_not_a_disclosure(
    advisor,
):
    """A term disclosure no longer follows R02 or R22: their terms are Glossary lines."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    for key in adv.RETIREMENT_A_KEYS:
        node = adv.explainer_nodes(shown.page, key)[0]
        following = adv.next_element(node)
        assert (
            following is None or following.name != "details" or not following.get("data-terms")
        ), f"{key} is followed by a term disclosure"
        assert not adv.disclosure_nodes(shown.page, key), f"{key} still has its own disclosure"


def test_every_old_text_the_retirement_tab_dropped_is_in_no_section_body(advisor):
    """S-3.3 for the tab: the reference text of each shortened row and each old sentence its
    new text drops appears in no section body; a sentence a Glossary line took over lives there."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    body = _without_glossaries(shown.page)
    values = _full_world_values()
    problems = []
    for key in adv.RETIREMENT_A_KEYS:
        old = adv.norm(adv.expected_row_at_reference(key, values))
        kept = set(_sentences(adv.expected_a(key, values)))
        if old in body:
            problems.append(f"{key}: the whole old text is on the page")
        for sentence in _sentences(old):
            if sentence not in kept and sentence in body:
                problems.append(f"{key}: the old sentence {sentence!r} is on the page")
    assert not problems, "\n".join(problems)
    assert OLD_R12 not in adv.norm(shown.page.get_text(" ")), "the old R12 sentence is on the page"


def test_the_retirement_tab_ends_with_one_closed_glossary_holding_table_gs_lines_in_order(
    advisor,
):
    """S-4.1 and S-4.2 for the tab: one disclosure, closed, summary Glossary, the last element of
    the tab panel, one paragraph per line in order with exactly the line's text (slots filled)."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    values = _full_world_values()
    panel = _retirement(shown)
    glossary = _glossary_of(panel)
    assert "hp-more" in glossary.get("class", []) and not glossary.has_attr("open")
    assert glossary.has_attr("data-glossary-host") and glossary.get("data-testid") == "glossary"
    summary = glossary.find("summary", recursive=False)
    assert summary is not None and adv.norm(summary.get_text(" ")) == "Glossary"
    assert [c for c in panel.find_all(recursive=False)][-1] is glossary, (
        "the Glossary is not the last element of the Retirement panel"
    )
    lines = [c for c in glossary.find_all(recursive=False) if c.name != "summary"]
    expected = copy_fixture.glossary("Retirement")
    assert [c.name for c in lines] == ["p"] * len(expected)
    assert [c.get("data-term") for c in lines] == [line["key"] for line in expected]
    for node, line in zip(lines, expected, strict=True):
        text = line["text"]
        for slot in line["slots"]:
            phrase, _ = adv.phrase_of(slot, line["key"])
            assert "{" + slot + "}" in text
            text = text.replace("{" + slot + "}", values[slot])
        assert adv.norm(node.get_text(" ")) == adv.norm(text), line["key"]


def test_the_run_recommender_line_reads_its_g2_text_and_the_others_equal_the_reference(advisor):
    """S-4.5 and S-4.7 for the tab: R12 is the line Table G2 rewrites; every other line reads the
    reference fixture's text."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    values = _full_world_values()
    glossary = _glossary_of(_retirement(shown))
    g2 = [x for x in copy_fixture.glossary("Retirement") if x["source"] == "G2"]
    assert [x["key"] for x in g2] == ["R12"]
    found = glossary.select('p[data-term="R12"]')
    assert len(found) == 1 and adv.norm(found[0].get_text(" ")) == adv.norm(g2[0]["text"])
    for line in copy_fixture.glossary("Retirement"):
        if line["source"] != "ref":
            continue
        node = glossary.select_one(f'p[data-term="{line["key"]}"]')
        assert node is not None, line["key"]
        assert adv.norm(node.get_text(" ")) == adv.norm(
            adv.expected_row(line["key"], "B", **values)
        ), line["key"]


@pytest.mark.parametrize("world", ["empty", "full"])
def test_the_retirement_glossary_holds_every_line_whatever_sections_render(advisor, world):
    shown = advisor.open(world, key=False)
    glossary = _glossary_of(_retirement(shown))
    keys = [c.get("data-term") for c in glossary.find_all("p", recursive=False)]
    assert keys == [line["key"] for line in copy_fixture.glossary("Retirement")]


def test_the_retirement_tab_carries_no_what_the_terms_mean_disclosure(advisor):
    """S-4.3 for the tab: no such summary, no data-terms but the Glossary, each term defined once."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    panel = _retirement(shown)
    summaries = [adv.norm(s.get_text(" ")) for s in panel.select("summary")]
    assert adv.DISCLOSURE_SUMMARY not in summaries, summaries
    others = [
        d["data-terms"] for d in panel.select("[data-terms]") if d["data-terms"] != "glossary"
    ]
    assert others == [], others
    for line in copy_fixture.glossary("Retirement"):
        found = shown.page.select(f'#tab-panel-retirement [data-term="{line["key"]}"]')
        assert len(found) == 1, f"{line['key']} is defined {len(found)} times in the tab"


def test_the_retirement_tab_hint_and_the_run_buttons_title_keep_their_words(advisor):
    """CX-12 (the hint stays) and S-4.9 (the button's own title stays)."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    hint = shown.page.select_one('[data-testid="retirement-run-now-hint"]')
    assert hint is not None and adv.norm(hint.get_text(" ")) == (
        "Run recommender is available outside market hours only — the Composer API is shared "
        "with the live engine."
    )
    button = shown.page.select_one('[data-testid="retirement-run-now-btn"]')
    assert button["title"] == (
        "Runs the same analysis as the nightly 03:45 UTC job; takes a minute or two."
    )


_TILE_LINES = (
    ("retirement-last-run-symphonies", "R06"),
    ("retirement-last-run-depth", "R07"),
    ("retirement-last-run-pairs", "R08"),
    ("retirement-last-run-blocked", "R09"),
)
_EVIDENCE_LINES = (
    ("performance", "R29a"),
    ("composite", "R29b"),
    ("redundancy", "R29c"),
    ("backtest", "R29d"),
    ("marginal", "R29e"),
    ("long_vix", "R29f"),
)


def test_the_run_tiles_and_the_evidence_headings_name_their_glossary_lines(advisor):
    """TG3-13 in the server render: each of the four tile labels and the six evidence headings
    carries the key of its line (a tile label such as 'Pairs screened at 0.65 or above' is not
    its line's lead term, so the match by text could not reach it)."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    panel = _retirement(shown)
    problems = []
    for testid, key in _TILE_LINES:
        label = panel.select_one(f'[data-testid="{testid}"] .mini-stat-label')
        if label is None:
            problems.append(f"{testid}: no label element")
        elif label.get("data-glossary") != key:
            problems.append(f"{testid}: data-glossary is {label.get('data-glossary')!r}, not {key}")
    assert not problems, "\n".join(problems)
    glossary = _glossary_of(panel)
    for _, key in (*_TILE_LINES, *_EVIDENCE_LINES):
        assert glossary.select_one(f'p[data-term="{key}"]') is not None, key


def test_every_evidence_heading_of_a_tenure_card_names_its_glossary_line(advisor):
    shown = advisor.open("empty", key=False, seed=lambda: pb.store_run(pb.full_record()))
    cards = shown.page.select('[data-testid="tenure-review-entry"]')
    assert cards, "premise: a stored review renders cards"
    for card in cards:
        for name, key in _EVIDENCE_LINES:
            heading = card.select_one(
                f'[data-testid="tenure-review-evidence-{name}"] [data-testid="tenure-review-evidence-heading"]'
            )
            assert heading is not None, name
            assert heading.get("data-glossary") == key, (name, heading.get("data-glossary"))


# ---------------------------------------------------------------------------
# Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder and Frontrunner Builder:
# what the plan removes or rewords, the unavailable states, and the Glossary keys on labels
# (the plan's AC-3 and AC-4 for the six tabs)
# ---------------------------------------------------------------------------

# The unavailable bodies as the reference page printed them (templates/ai_advisor.html at
# c0760e70): the plan keeps them (S-3.7).
UNAVAILABLE_BODIES = {
    "no-api-key-state": (
        "Composer API key not configured. Set COMPOSER_KEY_ID and COMPOSER_SECRET to enable "
        "swap proposals."
    ),
    "no-api-key-state-logic": (
        "Composer API key not configured. Set COMPOSER_KEY_ID and COMPOSER_SECRET to enable "
        "logic-change proposals."
    ),
    "no-api-key-state-sb": (
        "Composer API key not configured. Set COMPOSER_KEY_ID and COMPOSER_SECRET environment "
        "variables to enable strategy-builder proposals."
    ),
}
# The Strategy Builder banner sentence CX-9 drops (it is Glossary B02), as the reference printed it.
OLD_SB_BANNER_SENTENCE = (
    "A multiple-testing correction (FDR/Yekutieli) is applied across ALL backtested candidates "
    "— raising the number of candidates raises the bar each must clear."
)
OLD_FR_EMPTY_BODY = "Run a build above, or check back after the weekly scheduled run."
OLD_BANNER_TEXT_FR = "Review each candidate's deltas below before approving."


def test_every_old_text_the_other_tabs_dropped_is_in_no_section_body(advisor):
    """S-3.3 for the six tabs: the reference text of each shortened or removed row, and each old
    sentence its new text drops, appears in no section body (hidden panels included)."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    body = _without_glossaries(shown.page)
    problems = []
    for key in (*adv.OTHER_TAB_A_KEYS, "B03", "X05"):
        old = adv.norm(copy_fixture.text(key, "A"))
        new = copy_fixture.a_text(key)
        kept = set(_sentences(new)) if new else set()
        if old in body:
            problems.append(f"{key}: the whole old text is on the page")
        for sentence in _sentences(old):
            if sentence not in kept and sentence in body:
                problems.append(f"{key}: the old sentence {sentence!r} is on the page")
    for literal in (OLD_SB_BANNER_SENTENCE, OLD_FR_EMPTY_BODY):
        if literal in body:
            problems.append(f"the old text {literal!r} is on the page")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("world", ["full", "empty"])
def test_the_removed_descriptions_are_not_on_the_page_in_any_state(advisor, world):
    shown = advisor.open(
        world, key=world == "full", seed=_seed_everything if world == "full" else None
    )
    for removed in ("B03", "X05"):
        assert adv.explainer_nodes(shown.page, removed) == [], f"{removed} shows in {world}"
    assert not shown.page.select('[data-terms="B03"], [data-terms="X05"]')


def test_the_strategy_builder_banner_reads_exactly_the_shortened_text_and_stays_a_note(advisor):
    """S-3.6 and CX-9: the multiple-testing sentence is gone, the other three stay word for word."""
    shown = advisor.open("full", key=True, seed=_seed_everything)
    banner = shown.page.select_one('[data-testid="strategy-builder-risk-banner"]')
    assert banner is not None and banner.get("role") == "note"
    assert adv.norm(banner.get_text(" ")) == copy_fixture.x_row("CX-9")["new_text"]
    assert not banner.select("button, input, a"), "the banner is not dismissible"
    assert banner.select_one("strong") is not None, "the bold lead stays"


def test_the_strategy_builder_still_shows_its_model_line_and_b00_under_it(advisor):
    shown = advisor.open("full", key=True, models=True, seed=_seed_everything)
    panel = adv.panel_of(shown.page, "strategy-builder")
    line = panel.select_one(".cap-tab-attribution")
    assert line is not None and adv.norm(line.get_text(" ")) == adv.norm(
        copy_fixture.fill(copy_fixture.row("MU4", "C")["new_resolved"], model=SUGGESTION_MODEL)
    )
    assert adv.next_element(line) is adv.explainer_nodes(shown.page, "B00")[0]


def test_the_frontrunner_empty_state_body_reads_the_reworded_text(advisor):
    shown = advisor.open("empty", key=True)
    state = shown.page.select_one('[data-testid="frontrunner-builder-empty-state"]')
    assert state is not None, "premise: no pending proposals renders the empty state"
    body = state.select_one(".empty-state-body")
    assert adv.norm(body.get_text(" ")) == copy_fixture.x_row("CX-11")["new_text"]
    assert copy_fixture.x_row("CX-11")["new_text"] == "Proposals appear here after a build."
    title = state.select_one(".empty-state-title")
    assert adv.norm(title.get_text(" ")) == "No pending frontrunner proposals."


@pytest.mark.parametrize(
    ("testid", "body"), list(UNAVAILABLE_BODIES.items()), ids=list(UNAVAILABLE_BODIES)
)
def test_without_a_key_each_unavailable_state_reads_as_at_the_reference_and_the_tab_lines_show(
    advisor, testid, body
):
    """S-3.7: W01, L01 and B00 still show; the unavailable state is the reference's."""
    shown = advisor.open("full", key=False, seed=_seed_everything)
    for key in ("W01", "L01", "B00"):
        node = adv.explainer_nodes(shown.page, key)
        assert len(node) == 1 and adv.norm(node[0].get_text(" ")) == adv.norm(
            copy_fixture.a_text(key)
        ), key
    state = shown.page.select_one(f'[data-testid="{testid}"] .no-api-key-body')
    assert state is not None, f"no {testid} on a page with no key"
    assert adv.norm(state.get_text(" ")) == body


def test_the_chat_default_body_reads_t02_and_names_both_buttons_that_open_a_discussion(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    node = shown.page.select_one('.chat-default-state [data-explainer="T02"]')
    assert node is not None
    text = adv.norm(node.get_text(" "))
    assert text == copy_fixture.a_text("T02")
    assert "Discuss this proposal" in text and "Chat about this" in text


# --- the Glossary keys on labels (TG3-15), in the server render

_FR_LABEL_LINES = {"Calmar": "X11", "CAGR": "X11", "Max DD": "X11", "Node count change": "X12"}


def _key_of_cell(cell) -> str | None:
    holder = cell if cell.has_attr("data-glossary") else cell.select_one("[data-glossary]")
    return holder.get("data-glossary") if holder is not None else None


def test_the_frontrunner_change_labels_name_their_glossary_lines(advisor):
    shown = advisor.open("empty", key=True, seed=lambda: adv.seed_frontrunner_proposals_varied(2))
    cards = shown.page.select('[data-testid="frontrunner-proposal-card"]')
    assert len(cards) == 2, "premise: both proposals render a card"
    seen = set()
    for card in cards:
        for row in card.select('[data-testid="stats-table"] tbody tr'):
            cell = row.find(["td", "th"])
            label = adv.norm(cell.get_text(" "))
            if label in _FR_LABEL_LINES:
                seen.add(label)
                assert _key_of_cell(cell) == _FR_LABEL_LINES[label], (
                    f"{label!r} names {_key_of_cell(cell)!r}, not {_FR_LABEL_LINES[label]}"
                )
    assert seen == set(_FR_LABEL_LINES), f"labels never found: {set(_FR_LABEL_LINES) - seen}"
    glossary = adv.panel_of(shown.page, "frontrunner-builder").select_one(
        'details[data-terms="glossary"]'
    )
    for key in set(_FR_LABEL_LINES.values()):
        assert glossary.select_one(f'p[data-term="{key}"]') is not None, key


def test_the_live_baseline_header_names_its_glossary_line(advisor):
    shown = advisor.open("full", key=True, seed=lambda: adv.seed_strategy_builder_cards(3))
    headers = shown.page.select('[data-testid="baseline-col-header"]')
    assert headers, "premise: a survivor with a live baseline renders the column"
    assert {h.get("data-glossary") for h in headers} == {"B07t"}
    glossary = adv.panel_of(shown.page, "strategy-builder").select_one(
        'details[data-terms="glossary"]'
    )
    assert glossary.select_one('p[data-term="B07t"]') is not None


# --- the Frontrunner cards sit in the equal-track grid (TT-26)


def test_the_frontrunner_cards_sit_in_the_equal_track_grid_and_no_fill_rule_is_left(advisor):
    shown = advisor.open("full", key=True, seed=lambda: adv.seed_frontrunner_proposals(5))
    grid = shown.page.select_one('[data-testid="frontrunner-proposal-cards"]')
    assert grid is not None and "proposal-cards" in grid.get("class", [])
    assert "proposal-cards--fill" not in grid.get("class", []), (
        "the flex wrap that stretches a last row"
    )
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "proposal-cards--fill" not in source, "a rule or class of the fill modifier is left"
