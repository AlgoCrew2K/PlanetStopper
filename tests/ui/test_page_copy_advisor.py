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


A_KEYS_WITH_TERMS = [k for k in adv.OWNED_A_KEYS if adv.b_rows_for(k)]


def test_every_owned_row_is_in_the_fixture_and_every_term_row_has_a_description():
    owned_b = [r for r in copy_fixture.rows("B") if r["host"] in adv.TAB_OF_B_HOST]
    assert owned_b, "the fixture holds no Advisor term rows"
    for record in owned_b:
        assert record["placement_target"] in adv.OWNED_A_KEYS, (
            f"{record['key']} is placed under {record['placement_target']}, which no Advisor row owns"
        )
    for key in adv.OWNED_A_KEYS:
        copy_fixture.row(key, "A")


# ---------------------------------------------------------------------------
# AC-1: descriptions (S-1.7 .. S-1.14, S-1.19, S-1.23)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", adv.OWNED_A_KEYS)
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
    for key in adv.OWNED_A_KEYS:
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
        "overview": ("O01",),
        "correlations": ("C01",),
        "asset-swaps": ("W01",),
        "logic-changes": ("L01",),
        "chat": ("T02",),
        "strategy-builder": ("B00",),
        "frontrunner-builder": ("X01", "X05"),
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
    present = {k for k in adv.OWNED_A_KEYS if adv.explainer_nodes(shown.page, k)}
    for key in ("A01", "O01", "O03", "O08", "O15", "O27", "C01", "R01", "R02", "R22"):
        assert key in present, f"{key}: a rendered section lost its description"
    for key in ("W01", "L01", "B00", "T02", "X01"):
        assert key in present, f"{key} is a tab line and always shows"
    for key in ("B07", "B10", "X05"):
        assert key not in present, f"{key} describes a section that does not render"
        assert not adv.disclosure_nodes(shown.page, key), (
            f"{key}: a disclosure outlives its section"
        )
    for row in adv.b_rows_for("X05") + adv.b_rows_for("B07") + adv.b_rows_for("B10"):
        assert not shown.page.select(f'[data-term="{row["key"]}"]'), (
            f"{row['key']} is defined for a section that is not on the page"
        )


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
# AC-2: term definitions (S-2.6 .. S-2.11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", A_KEYS_WITH_TERMS)
def test_a_disclosure_follows_its_description_closed_with_one_line_per_term(advisor, key):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    values = _full_world_values()
    rows = adv.b_rows_for(key)
    nodes = adv.disclosure_nodes(shown.page, key)
    assert len(nodes) == 1, f"{key}: {len(nodes)} disclosures"
    details = nodes[0]
    assert "hp-more" in details.get("class", []), f"{key}: the disclosure is not the shared hp-more"
    assert not details.has_attr("open"), f"{key}: the disclosure is open on load"
    summary = details.find("summary", recursive=False)
    assert summary is not None and adv.norm(summary.get_text(" ")) == adv.DISCLOSURE_SUMMARY
    explainer = adv.explainer_nodes(shown.page, key)[0]
    assert adv.next_element(explainer) is details, (
        f"{key}: the disclosure is not directly after its description"
    )
    lines = [c for c in details.find_all(recursive=False) if c.name != "summary"]
    assert [c.name for c in lines] == ["p"] * len(rows), (
        f"{key}: the disclosure holds {[c.name for c in lines]}, not one p per term row"
    )
    assert [c.get("data-term") for c in lines] == [r["key"] for r in rows], (
        f"{key}: the term rows are not in table order"
    )
    for line, record in zip(lines, rows, strict=True):
        assert adv.norm(line.get_text(" ")) == adv.norm(
            adv.expected_row(record["key"], "B", **values)
        ), record["key"]


def test_every_term_is_defined_once_on_the_page(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    for record in (r for r in copy_fixture.rows("B") if r["host"] in adv.TAB_OF_B_HOST):
        found = shown.page.select(f'[data-term="{record["key"]}"]')
        assert len(found) == 1, f"{record['key']} is defined {len(found)} times on the page"


def test_every_disclosure_is_closed_on_load_and_has_the_one_summary_everywhere(advisor):
    shown = advisor.open("full", key=True, seed=_seed_everything)
    details = shown.page.select("details.hp-more")
    assert details, "the page holds no shared disclosure"
    for node in details:
        assert not node.has_attr("open")
        assert adv.norm(node.find("summary").get_text(" ")) == adv.DISCLOSURE_SUMMARY


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


def test_the_market_prism_badge_reads_its_resolved_and_unresolved_forms(advisor):
    record = copy_fixture.row("MU1", "C")
    resolved = advisor.open("full", key=True, models=True)
    badge = resolved.page.select_one('[data-testid="prism-model-badge"]')
    assert adv.norm(badge.get_text(" ")) == adv.norm(
        copy_fixture.fill(record["new_resolved"], model=SYNTHESIS_MODEL)
    )
    unresolved = advisor.open("full", key=True, models=False)
    badge = unresolved.page.select_one('[data-testid="prism-model-badge"]')
    assert adv.norm(badge.get_text(" ")) == adv.norm(record["new_unresolved"])


@pytest.mark.parametrize("resolved", [True, False])
def test_the_strategy_builder_run_note_reads_the_kept_note_with_the_models_state(advisor, resolved):
    shown = advisor.open("full", key=True, models=resolved, seed=_seed_everything)
    node = adv.explainer_nodes(shown.page, "B03")[0]
    assert adv.norm(node.get_text(" ")) == adv.norm(
        adv.kept_model_note("B03", SUGGESTION_MODEL if resolved else None)
    )


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
        assert any(m.group(1) == "terms" for m in matches), "the template carries no disclosure"
        for match in matches:
            window = source[match.start() : match.start() + 600]
            assert "| safe" not in window and "|safe" not in window, match.group(0)

    def test_a_description_carries_no_script_or_event_handler(self, advisor):
        shown = advisor.open("full", key=True, seed=_seed_everything)
        nodes = shown.page.select("[data-explainer], details.hp-more")
        assert len(nodes) >= len(adv.OWNED_A_KEYS), "the page carries no descriptions to scan"
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
