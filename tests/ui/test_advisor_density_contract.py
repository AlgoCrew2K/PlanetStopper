"""The AI Advisor page's density and type contract.

Subject: ``GET /ai-advisor`` (the server render), the page's inline stylesheet, and the real
``static/ai_advisor*.js`` renderers under node.

* The autotune runs render as one table row per run, never one card per run.
* The Market Prism sources sit behind one closed disclosure that counts them and keeps the
  per-lens grouping inside; the lens summaries are one row of five.
* The Frontrunner Builder says what every card shares once, above the list, and a card carries
  only what differs (the incumbent's name, its overlay, its figures).
* The tab bar is one line per tab: the model attribution sits in the tab's own panel.
* No label on this page is upper-case, no meta text is italic, and no weight above 600 is written.
* An outcome is a status dot and one word, never a bordered pill.

Fixture provenance: the run rows are labelled constructions (the keys the route returns, with
made-up values); the Prism row is the producer's own (``tests/_advisor_overview_prism.py``); the
Frontrunner proposal is the real writer's row over the captured incumbent tree
(``tests/ui/_shell_support.py``).
"""

from __future__ import annotations

import pathlib
import re

import pytest

import database
from tests._advisor_overview_prism import prism_raw_response
from tests._ai_advisor_autotune_panel_harness import drive
from tests._ux_truth_support import parse_html
from tests.app import _retirement_tab_world as w
from tests.ui import _dashboard_primitives_capture as cap
from tests.ui._shell_support import seed_pending_frontrunner_proposal

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "ai_advisor.html"
STATIC = REPO / "static"
ADVISOR_SCRIPTS = sorted(STATIC.glob("ai_advisor*.js"))

_SHARED_APPROVAL_TEXT = "Approve creates a NEW undeployed Composer symphony"


def _inline_css() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    return "\n".join(
        re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.IGNORECASE | re.DOTALL)
    )


def _page(client) -> str:
    return w.get_page(client)


def _inside(node, ancestor) -> bool:
    cursor = node
    while cursor is not None:
        if cursor is ancestor:
            return True
        cursor = cursor.parent
    return False


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "density.db"))
    database.init_db()
    database.run_migrations()
    return database.DB_FILE


# ---------------------------------------------------------------------------
# Autotune runs: one row each
# ---------------------------------------------------------------------------


def _run_row(row_id: int, symphony: str, **extra) -> dict:
    row = {
        "id": row_id,
        "symphony_id": symphony,
        "symphony_name": symphony.title(),
        "run_timestamp": "2026-10-30T22:33:00",
        "baseline_decision": "fallback",
        "frozen_eval_verdict": None,
        "naive_sharpe": None,
        "selection_tstat": None,
        "objective": "crra_eu",
        "never_adopted_streak": {"status": "streak", "streak_weeks": 4},
    }
    row.update(extra)
    return row


class TestAutotuneRunsAreRows:
    def _list_html(self, rows) -> str:
        snapshots = drive([{"op": "load"}], {"/api/autotune-runs": rows})
        return snapshots[-1]["list"]

    def test_each_symphony_is_one_table_row_and_none_is_a_card(self):
        html = self._list_html([_run_row(i, f"symphony-{i}") for i in range(1, 6)])
        assert html.count('data-testid="autotune-run-row"') == 5
        assert html.count('role="row"') == 6, "five runs and one header row"
        assert "sym-card" not in html and "autotune-run-card" not in html

    def test_a_row_carries_the_six_columns_the_header_names(self):
        html = self._list_html([_run_row(1, "alpha")])
        for cell in (
            "autotune-run-name",
            "autotune-run-time",
            "autotune-run-outcome",
            "autotune-run-streak",
            "autotune-run-score",
            "autotune-run-tstat",
        ):
            assert f'class="{cell}"' in html, cell
        for header in ("Symphony", "Run time (ET)", "Outcome", "Best score", "Sel t-stat"):
            assert f">{header}</span>" in html, header

    def test_the_outcome_is_a_dot_and_one_word_with_no_inline_colour(self):
        html = self._list_html([_run_row(1, "alpha")])
        assert 'class="decision-pill decision-pill--warn">Fallback</span>' in html
        assert "style=" not in html, "an outcome is coloured by its class, never an inline colour"

    def test_the_streak_keeps_its_full_sentence_for_a_reader_of_one_row(self):
        html = self._list_html([_run_row(1, "alpha")])
        text = re.sub(r"<[^>]+>", "", html)
        assert "4 consecutive runs without adopting a tuned config" in text

    def test_a_run_that_was_never_frozen_evaluated_says_so_without_a_visible_line(self):
        html = self._list_html([_run_row(1, "alpha")])
        assert "Frozen-eval not evaluated" in html
        assert re.search(r'class="autotune-run-frozen adv-vh">Frozen-eval not evaluated', html)

    def test_the_older_runs_use_the_same_rows_without_a_second_header(self):
        rows = [_run_row(1, "alpha"), _run_row(2, "alpha", run_timestamp="2026-10-23T22:33:00")]
        snapshots = drive([{"op": "load"}], {"/api/autotune-runs": rows})
        older = snapshots[-1]["older"]["html"]
        assert older.count('data-testid="autotune-run-row"') == 1
        assert "autotune-row--head" not in older


# ---------------------------------------------------------------------------
# Market Prism: sources collapsed, lenses in one row
# ---------------------------------------------------------------------------


def _store_prism_row_with_sources() -> int:
    raw = prism_raw_response()
    raw["per_lens_digest"]["macro"]["sources"] = ["FRED DGS10", "FRED UNRATE"]
    raw["per_lens_digest"]["derivatives"]["sources"] = ["CBOE VIX term structure"]
    return database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="global",
        verdict=raw["overall_sentiment"],
        raw_response=raw,
    )


class TestMarketPrismIsCompact:
    def test_the_sources_are_one_closed_disclosure_that_counts_them(self, isolated_db, client):
        w.seed_roster()
        _store_prism_row_with_sources()
        root = parse_html(_page(client))
        sources = root.find(testid="prism-sources")
        assert sources is not None and sources.tag == "details"
        assert "open" not in sources.attrs, "the sources open only when the operator opens them"
        summary = sources.find_all("summary")
        cards = sources.find_all(cls="prism-source-card")
        assert len(cards) >= 3, "premise: the three sources the test added are cards"
        assert summary and summary[0].text.strip() == f"Sources ({len(cards)})"

    def test_the_per_lens_grouping_stays_inside_the_disclosure(self, isolated_db, client):
        w.seed_roster()
        _store_prism_row_with_sources()
        sources = parse_html(_page(client)).find(testid="prism-sources")
        assert sources.find(testid="prism-sources-lens-macro") is not None
        assert sources.find(testid="prism-sources-lens-derivatives") is not None

    def test_a_lens_names_itself_in_sentence_case_with_its_status_beside_it(
        self, isolated_db, client
    ):
        w.seed_roster()
        _store_prism_row_with_sources()
        lens = parse_html(_page(client)).find(testid="prism-lens-technicals")
        head = lens.find_all(cls="prism-lens-head")
        assert head, "a lens has a head line holding its name and status"
        assert lens.find_all(cls="prism-lens-name")[0].text.strip() == "Technicals"
        assert lens.find_all(cls="prism-lens-status")

    def test_the_verdict_sits_on_the_title_line_not_above_the_rationale(self, isolated_db, client):
        w.seed_roster()
        _store_prism_row_with_sources()
        block = parse_html(_page(client)).find(testid="market-prism-block")
        header = block.find_all(cls="prism-block-header")[0]
        assert header.find(testid="prism-sentiment-chip") is not None


# ---------------------------------------------------------------------------
# Frontrunner Builder: the shared text once
# ---------------------------------------------------------------------------


class TestFrontrunnerSharedTextOnce:
    def test_what_every_card_shares_appears_once_above_the_cards(self, isolated_db, client):
        for _ in range(3):
            seed_pending_frontrunner_proposal()
        html = _page(client)
        panel = html[html.index('data-testid="tab-panel-frontrunner-builder"') :]
        assert panel.count(_SHARED_APPROVAL_TEXT) == 1, "the approval sentence repeats per card"
        assert panel.count("Each frontrunner proposal is a full standalone copy") == 1
        shared = panel.index('data-testid="fr-shared-notes"')
        first_card = panel.index('data-testid="frontrunner-proposal-card"')
        assert shared < first_card, "the shared text sits above the list"
        assert panel.count('data-testid="frontrunner-proposal-card"') == 3

    def test_a_card_carries_its_incumbent_overlay_and_figures_and_no_source_pill(
        self, isolated_db, client
    ):
        seed_pending_frontrunner_proposal()
        root = parse_html(_page(client))
        card = root.find_all(testid="frontrunner-proposal-card")[0]
        assert card.find(testid="fr-incumbent-name") is not None
        assert card.find(testid="fr-overlay-summary") is not None
        assert card.find(testid="fr-node-count-delta") is not None
        assert card.find(testid="proposal-source-badge") is None, "the usual source needs no pill"
        assert (
            card.find(testid="fr-explainer-line") is None
            and card.find(testid="fr-identity-note") is None
        )

    def test_the_node_count_is_a_row_of_the_figures_table(self, isolated_db, client):
        seed_pending_frontrunner_proposal()
        card = parse_html(_page(client)).find_all(testid="frontrunner-proposal-card")[0]
        row = card.find(testid="fr-node-count-delta")
        table = card.find(testid="stats-table")
        assert row.tag == "tr" and _inside(row, table)

    def test_the_risk_banner_and_both_actions_stay(self, isolated_db, client):
        seed_pending_frontrunner_proposal()
        root = parse_html(_page(client))
        assert root.find(testid="frontrunner-builder-risk-banner") is not None
        card = root.find_all(testid="frontrunner-proposal-card")[0]
        assert (
            card.find(testid="fr-approve-btn") is not None
            and card.find(testid="fr-reject-btn") is not None
        )


# ---------------------------------------------------------------------------
# The tab bar is one line per tab
# ---------------------------------------------------------------------------


class TestTabBarIsOneLine:
    def test_no_tab_button_holds_a_second_caption_line(self, isolated_db, client):
        root = parse_html(_page(client))
        tabs = [n for n in root.find_all("button") if n.attrs.get("role") == "tab"]
        assert len(tabs) == 8
        for tab in tabs:
            assert tab.find_all(cls="cap-tab-attribution") == [], tab.attrs.get("data-tab")

    @pytest.mark.parametrize("name", ["asset-swaps", "logic-changes", "chat", "strategy-builder"])
    def test_the_attribution_sits_at_the_head_of_the_tabs_own_panel(
        self, isolated_db, client, name
    ):
        panel = parse_html(_page(client)).find(testid=f"tab-panel-{name}")
        assert panel is not None
        notes = panel.find_all(cls="cap-tab-attribution")
        assert len(notes) == 1, name


# ---------------------------------------------------------------------------
# Type: no upper-case labels, no italics, quiet weights, no pills
# ---------------------------------------------------------------------------


def _declarations() -> list[tuple[str, str, str]]:
    return [
        (", ".join(selectors), prop, value)
        for _ctx, selectors, decls in cap.iter_rules(_inline_css())
        for prop, value in decls
    ]


class TestTypeIsQuiet:
    def test_no_rule_on_this_page_sets_an_uppercase_transform(self):
        offenders = [
            (sel, value)
            for sel, prop, value in _declarations()
            if prop == "text-transform" and value.strip() not in ("none",)
        ]
        assert offenders == []

    def test_no_script_on_this_page_writes_an_uppercase_transform(self):
        offenders = [
            script.name
            for script in ADVISOR_SCRIPTS
            if re.search(r"text-transform\s*:\s*uppercase", script.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_no_label_is_tracked_out(self):
        offenders = [
            (sel, value)
            for sel, prop, value in _declarations()
            if prop == "letter-spacing" and value.strip() not in ("0", "normal")
        ]
        assert offenders == []

    def test_no_meta_text_is_italic(self):
        offenders = [
            sel
            for sel, prop, value in _declarations()
            if prop == "font-style" and "italic" in value
        ]
        assert offenders == []
        for script in ADVISOR_SCRIPTS:
            assert "font-style:italic" not in script.read_text(encoding="utf-8").replace(" ", "")

    def test_no_weight_above_600_is_written(self):
        heavy = [
            (sel, value)
            for sel, prop, value in _declarations()
            if prop == "font-weight"
            and (value.strip() == "bold" or int(re.sub(r"\D", "", value) or 0) > 600)
        ]
        assert heavy == []
        for script in ADVISOR_SCRIPTS:
            assert not re.search(
                r"font-weight\s*:\s*(?:[7-9]00|bold)", script.read_text(encoding="utf-8")
            ), script.name

    def test_no_label_is_set_below_eleven_pixels(self):
        small = []
        for sel, prop, value in _declarations():
            if prop != "font-size":
                continue
            match = re.fullmatch(r"([\d.]+)(px|rem)", value.strip())
            if not match:
                continue
            px = float(match.group(1)) * (16 if match.group(2) == "rem" else 1)
            if px < 11:
                small.append((sel, value))
        assert small == []

    def test_an_outcome_is_a_dot_and_a_word_never_a_bordered_pill(self):
        css = _inline_css()
        for name in ("decision-pill", "gate-pill", "tenure-verdict-chip", "prism-sentiment-chip"):
            rules = [
                (sel, prop, value)
                for sel, prop, value in _declarations()
                if re.search(rf"\.{name}(?![\w-])", sel) and "::before" not in sel
            ]
            assert rules, name
            assert not [
                r for r in rules if r[1] == "border" and r[2].strip() not in ("0", "none")
            ], name
            assert not [r for r in rules if r[1] == "border-radius" and "999" in r[2]], name
        assert ".decision-pill::before" in css, "the outcome's dot"
