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
from tests.app import _tenure_review_panel_builders as pb
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


def _declared(selector_fragment: str, prop: str) -> str | None:
    """The last top-level value the page's stylesheet declares for ``prop`` on a rule whose
    selector contains ``selector_fragment`` (None when no such rule declares it)."""
    value = None
    for ctx, selectors, decls in cap.iter_rules(_inline_css()):
        if ctx or not any(selector_fragment in sel for sel in selectors):
            continue
        for name, val in decls:
            if name == prop:
                value = val.strip()
    return value


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
        # One row has no sentence, and a score and a t-stat that are figures keep their columns.
        html = self._list_html([_run_row(1, "alpha", naive_sharpe=1.2, selection_tstat=2.1)])
        row = parse_html(html).find_all(testid="autotune-run-row")[0]
        for cell in (
            "autotune-run-name",
            "autotune-run-time",
            "autotune-run-outcome",
            "autotune-run-streak",
            "autotune-run-score",
            "autotune-run-tstat",
        ):
            assert row.find_all(cls=cell), cell
        heads = [n.text for n in parse_html(html).find_all(cls="autotune-row--head")[0].iter()]
        for header in (
            "Symphony",
            "Run time (ET)",
            "Outcome",
            "Runs without adopting a tuned config",
            "Best CRRA-EU score",
            "Sel t-stat",
        ):
            assert header in heads, header

    def test_a_score_and_a_t_stat_that_read_na_in_every_row_have_no_column(self):
        html = self._list_html([_run_row(i, f"symphony-{i}") for i in range(1, 4)])
        root = parse_html(html)
        assert not root.find_all(cls="autotune-run-score")
        assert not root.find_all(cls="autotune-run-tstat")
        assert "N/A" not in root.text

    def test_the_outcome_is_a_dot_and_one_word_with_no_inline_colour(self):
        html = self._list_html([_run_row(1, "alpha")])
        pills = parse_html(html).find_all(cls="decision-pill")
        assert [p.text for p in pills] == ["Fallback"]
        assert "decision-pill--warn" in pills[0].classes
        # The one inline style the table may carry is its column count, never a colour.
        styles = re.findall(r"""style\s*=\s*["']([^"']*)["']""", html)
        assert all(re.fullmatch(r"\s*--cols:\s*\d+;?\s*", s) for s in styles), (
            f"an outcome is coloured by its class, never an inline colour: {styles!r}"
        )

    def test_the_header_carries_the_streak_sentence_and_a_cell_carries_the_figure(self):
        root = parse_html(self._list_html([_run_row(1, "alpha")]))
        head = root.find_all(cls="autotune-row--head")[0]
        assert head.find_all(cls="autotune-run-streak")[0].text == (
            "Runs without adopting a tuned config"
        )
        cell = root.find_all(testid="autotune-run-row")[0].find_all(cls="autotune-run-streak")[0]
        assert cell.find_all(cls="autotune-streak-figure")[0].text == "4"
        # The row read alone still says the whole sentence; it shows on a phone, which has no header.
        assert cell.text == "4 runs without adopting a tuned config"
        tail = cell.find_all(cls="autotune-streak-tail")[0]
        assert "adv-vh" in tail.classes

    def test_a_streak_below_two_runs_renders_an_empty_cell(self):
        rows = [_run_row(1, "alpha", never_adopted_streak={"status": "streak", "streak_weeks": 1})]
        root = parse_html(self._list_html(rows))
        cell = root.find_all(testid="autotune-run-row")[0].find_all(cls="autotune-run-streak")[0]
        assert cell.text == ""

    def test_an_unknown_streak_keeps_its_informative_text(self):
        rows = [_run_row(1, "alpha", never_adopted_streak={"status": "insufficient_history"})]
        root = parse_html(self._list_html(rows))
        cell = root.find_all(testid="autotune-run-row")[0].find_all(cls="autotune-run-streak")[0]
        assert cell.text == "adoption streak: insufficient history (<2 runs)"

    def test_a_run_that_was_never_frozen_evaluated_says_so_in_visible_dim_ink(self):
        root = parse_html(self._list_html([_run_row(1, "alpha")]))
        row = root.find_all(testid="autotune-run-row")[0]
        frozen = row.find_all(cls="autotune-run-frozen")[0]
        assert frozen.text == "Frozen-eval not evaluated"
        assert "adv-vh" not in frozen.classes, "the reason there is no verdict is on screen"
        assert not [n for n in frozen.iter() if "adv-vh" in n.classes]
        assert _declared("autotune-run-frozen", "color") == "var(--studio-ink-dim)"
        for prop, value in (
            ("display", "none"),
            ("visibility", "hidden"),
            ("clip", "rect(0 0 0 0)"),
        ):
            assert _declared("autotune-run-frozen", prop) != value, prop

    def test_the_score_column_header_names_the_objective_the_rows_share(self):
        # Scores that are figures keep the column in the table; a column that reads N/A in every row
        # has no cell and no header.
        rows = [_run_row(1, "alpha", naive_sharpe=1.2), _run_row(2, "beta", naive_sharpe=1.4)]
        root = parse_html(self._list_html(rows))
        head = root.find_all(cls="autotune-row--head")[0]
        score = head.find_all(cls="autotune-run-score")[0]
        assert score.text == "Best CRRA-EU score"
        assert not [n for n in score.iter() if "adv-vh" in n.classes], "named on screen"
        assert score.title and "CRRA expected-utility" in score.title
        table = root.find_all(cls="autotune-table")[0]
        assert "autotune-table--row-objectives" not in table.classes

    def test_a_sortino_run_names_its_objective_in_the_header_too(self):
        rows = [_run_row(1, "alpha", objective="sortino_loss_aversion", naive_sharpe=1.1)]
        head = parse_html(self._list_html(rows)).find_all(cls="autotune-row--head")[0]
        assert head.find_all(cls="autotune-run-score")[0].text == "Best Sortino score"

    def test_rows_that_differ_in_objective_each_name_their_own_on_screen(self):
        rows = [
            _run_row(1, "alpha", naive_sharpe=1.1),
            _run_row(2, "beta", objective="sortino_loss_aversion", naive_sharpe=1.2),
            _run_row(3, "gamma", objective=None, naive_sharpe=1.3),
        ]
        root = parse_html(self._list_html(rows))
        table = root.find_all(cls="autotune-table")[0]
        assert "autotune-table--row-objectives" in table.classes
        head = root.find_all(cls="autotune-row--head")[0]
        assert head.find_all(cls="autotune-run-score")[0].text == "Best score"
        labels = [
            row.find_all(cls="autotune-score-label")[0].text
            for row in root.find_all(testid="autotune-run-row")
        ]
        assert labels == ["Best CRRA-EU score", "Best Sortino score", "Best objective score"]
        # That modifier is what un-hides the label above the figure at every width.
        assert (
            _declared(
                "autotune-table--row-objectives .autotune-run-score .autotune-score-label",
                "position",
            )
            == "static"
        )

    def test_a_phone_row_names_its_score_t_stat_and_streak_on_screen(self):
        shown = {
            ".autotune-run-score .autotune-score-label",
            ".autotune-run-tstat .adv-vh",
            ".autotune-run-streak .autotune-streak-tail",
        }
        css = _inline_css()
        phone = [
            (selectors, dict(decls))
            for ctx, selectors, decls in cap.iter_rules(css)
            if "max-width: 1000px" in ctx
        ]
        for selector in shown:
            rules = [d for sels, d in phone if selector in sels]
            assert rules, selector
            merged = {k: v for d in rules for k, v in d.items()}
            assert merged.get("position") == "static", selector
            assert merged.get("display") == "inline", selector
        assert ".autotune-run-score::before" not in css, "the generic Score prefix is gone"
        assert 'content: "Score "' not in css

    def test_the_older_runs_table_has_its_own_header_so_a_bare_figure_keeps_its_name(self):
        rows = [
            _run_row(1, "alpha"),
            _run_row(2, "alpha", run_timestamp="2026-10-23T22:33:00", naive_sharpe=1.2),
        ]
        snapshots = drive([{"op": "load"}], {"/api/autotune-runs": rows})
        older = parse_html(snapshots[-1]["older"]["html"])
        assert len(older.find_all(testid="autotune-run-row")) == 1
        head = older.find_all(cls="autotune-row--head")
        assert len(head) == 1, "the older table names its own columns"
        assert head[0].find_all(cls="autotune-run-streak")[0].text == (
            "Runs without adopting a tuned config"
        )
        assert head[0].find_all(cls="autotune-run-score")[0].text == "Best CRRA-EU score"


def _norm(text: str) -> str:
    return " ".join(text.split())


class TestEveryValueOfARowStaysOnScreen:
    """A cell moved into the sentence is still on screen, in the sentence.

    The rows are the real route's over runs written by ``database.save_autotune_run`` from the
    captured fixtures (``tests/ui/_page_explainers_advisor.py``); the expected values come from
    those rows, never from the markup under test. A column that reads N/A in every row has no
    value to keep.
    """

    # What the sentence says for each outcome the route sends, as the words of its clause.
    _CLAUSE_WORDS = {"fallback": "kept its previous settings"}

    def _render(self, rows: list[dict]) -> tuple[list[str], list[str], str]:
        html = self._list_html(rows)
        root = parse_html(html)
        row_texts = [_norm(n.text) for n in root.find_all(testid="autotune-run-row")]
        lines = [_norm(n.text) for n in root.find_all(testid="autotune-sentence")]
        return row_texts, lines, html

    @staticmethod
    def _list_html(rows) -> str:
        return drive([{"op": "load"}], {"/api/autotune-runs": rows})[-1]["list"]

    @pytest.mark.parametrize(
        ("count", "adopted_at"),
        [(11, None), (11, 4), (2, None), (1, None)],
        ids=["all identical", "one differs", "two rows", "one row"],
    )
    def test_each_rows_values_are_in_the_row_or_in_the_sentence(
        self, isolated_db, count, adopted_at
    ):
        from tests.ui import _page_explainers_advisor as pe

        pe.seed_autotune_runs(count, adopted_at=adopted_at)
        rows = pe.route_rows()
        row_texts, lines, _ = self._render(rows)
        assert len(row_texts) == count
        line = lines[0] if lines else ""
        assert len(lines) <= 1
        for data, text in zip(rows, row_texts, strict=True):
            name = data.get("symphony_name") or data["symphony_id"]
            assert name in text, f"the row lost its symphony {name!r}: {text!r}"
            assert "N/A" not in text, f"a column that reads N/A in every row is on screen: {text!r}"
        # The outcome is in every row, or in the sentence as the clause that says it.
        for data, text in zip(rows, row_texts, strict=True):
            token = data["baseline_decision"]
            word = {"fallback": "Fallback"}.get(token, token)
            clause = self._CLAUSE_WORDS.get(token)
            assert word in text or (clause is not None and clause in line.lower()), (
                f"the outcome {token!r} of {data['symphony_id']!r} is on screen nowhere: "
                f"row {text!r}, sentence {line!r}"
            )

    def test_a_value_moved_to_the_sentence_is_not_repeated_in_a_row(self, isolated_db):
        from tests.ui import _page_explainers_advisor as pe

        pe.seed_autotune_runs(11)
        row_texts, lines, _ = self._render(pe.route_rows())
        assert lines, "premise: an identical outcome makes a sentence"
        for text in row_texts:
            for value in ("Fallback", "Frozen-eval not evaluated", "N/A"):
                assert value not in text, f"{value!r} is in the sentence and in a row: {text!r}"

    def test_the_older_runs_keep_every_cell_and_never_print_a_sentence(self, isolated_db):
        from tests.ui import _page_explainers_advisor as pe

        pe.seed_autotune_runs(11, older_for=3)
        snapshots = drive([{"op": "load"}], {"/api/autotune-runs": pe.route_rows()})
        older = parse_html(snapshots[-1]["older"]["html"])
        rows = older.find_all(testid="autotune-run-row")
        assert len(rows) == 3, "premise: three older runs, identical in every cell"
        assert not older.find_all(testid="autotune-sentence")
        assert "Same for every run" not in snapshots[-1]["older"]["html"]
        for row in rows:
            text = _norm(row.text)
            assert "Fallback" in text and "Frozen-eval not evaluated" in text, text
            assert "N/A" not in text, (
                f"an older row shows a column that reads N/A in every row: {text!r}"
            )

    def test_the_sentence_is_text_on_screen_never_visually_hidden(self, isolated_db):
        from tests.ui import _page_explainers_advisor as pe

        pe.seed_autotune_runs(11)
        root = parse_html(self._list_html(pe.route_rows()))
        (sentence,) = root.find_all(testid="autotune-sentence")
        assert sentence.classes, "premise: the sentence has a class its rules can be read from"
        for cls in sentence.classes:
            for prop, value in (
                ("display", "none"),
                ("visibility", "hidden"),
                ("clip", "rect(0 0 0 0)"),
            ):
                assert _declared(f".{cls}", prop) != value, f"{cls}: {prop}"
            size = _declared(f".{cls}", "font-size")
            if size and size.endswith("px"):
                assert float(size[:-2]) >= 11, "below the page's 11 px text floor"


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
# Retirement tenure review: the long evidence lists sit behind closed disclosures
# ---------------------------------------------------------------------------

_EVIDENCE_ITEMS = ("performance", "composite", "redundancy", "backtest", "marginal", "long_vix")


def _inside_details(node) -> bool:
    cursor = node.parent
    while cursor is not None:
        if cursor.tag == "details":
            return True
        cursor = cursor.parent
    return False


class TestTenureEvidenceIsCollapsed:
    @pytest.fixture()
    def entries(self, isolated_db, client):
        pb.seed_bot_state()
        record = pb.store_full_run()
        panel = pb.panel_of(_page(client))
        return record, [pb.entry_node(panel, e["symphony_id"]) for e in record["entries"]], panel

    def test_every_evidence_item_is_a_closed_disclosure(self, entries):
        _record, nodes, _panel_node = entries
        checked = 0
        for node in nodes:
            for name in _EVIDENCE_ITEMS:
                block = node.find(f"tenure-review-evidence-{name}")
                details = [n for n in block.walk() if n.tag == "details"]
                assert len(details) == 1, name
                assert "open" not in details[0].attrs, (
                    f"{name} opens only when the operator opens it"
                )
                checked += 1
        assert checked >= 18

    def test_the_summary_is_the_heading_and_one_headline_line(self, entries):
        _record, nodes, _panel_node = entries
        for node in nodes:
            for name in _EVIDENCE_ITEMS:
                block = node.find(f"tenure-review-evidence-{name}")
                summary = [n for n in block.walk() if n.tag == "summary"]
                assert len(summary) == 1, name
                heading = summary[0].find("tenure-review-evidence-heading")
                assert heading is not None, name
                lines = [
                    n
                    for n in summary[0].walk()
                    if {"tenure-review-evidence-figure", "tenure-review-evidence-meta"}
                    & set(n.classes())
                ]
                assert len(lines) <= 1, f"{name}: a summary holds one headline, not a list"

    def test_an_available_item_leads_with_its_first_figure_and_keeps_the_rest_inside(self, entries):
        record, nodes, _panel_node = entries
        checked = 0
        for stored, node in zip(record["entries"], nodes, strict=True):
            item = stored["evidence"]["performance"]
            if item.get("status") != "available" or not item.get("figures"):
                continue
            block = node.find("tenure-review-evidence-performance")
            summary = [n for n in block.walk() if n.tag == "summary"][0]
            first = next(iter(item["figures"])).replace("_", " ")
            headline = [n for n in summary.walk() if "tenure-review-evidence-figure" in n.classes()]
            assert headline and headline[0].text().lower().startswith(first + ":"), first
            later = [
                n
                for n in block.walk()
                if "tenure-review-evidence-figure" in n.classes() and n not in headline
            ]
            assert later, "the other figures stay in the item"
            assert all(_inside_details(n) and not n.is_inside(summary) for n in later)
            checked += 1
        assert checked >= 1

    def test_an_item_that_is_not_available_leads_with_its_status_line(self, entries):
        record, nodes, _panel_node = entries
        seen = 0
        for stored, node in zip(record["entries"], nodes, strict=True):
            for name in _EVIDENCE_ITEMS:
                if stored["evidence"][name].get("status") == "available":
                    continue
                block = node.find(f"tenure-review-evidence-{name}")
                summary = [n for n in block.walk() if n.tag == "summary"][0]
                text = summary.text().lower()
                assert "not applicable" in text or "unavailable" in text, (name, text)
                seen += 1
        assert seen >= 1

    def test_the_provenance_lines_stay_in_the_page_inside_the_disclosure(self, entries):
        record, nodes, _panel_node = entries
        for stored, node in zip(record["entries"], nodes, strict=True):
            for name in _EVIDENCE_ITEMS:
                block = node.find(f"tenure-review-evidence-{name}")
                label = stored["evidence"][name]["label"]
                labels = [
                    n
                    for n in block.walk()
                    if "retirement-rec-basis" in n.classes() and label in n.text()
                ]
                assert labels and all(_inside_details(n) for n in labels), name

    def test_the_verdict_the_tenure_the_basis_and_the_missing_line_stay_visible(self, entries):
        record, nodes, _panel_node = entries
        for node in nodes:
            assert node.find("tenure-review-verdict-chip") is not None
            for testid in ("tenure-review-tenure", "tenure-review-basis"):
                line = node.find(testid)
                assert line is not None and not _inside_details(line), testid
            chip = node.find("tenure-review-verdict-chip")
            assert not _inside_details(chip)
        with_missing = [n for n in nodes if n.find("tenure-review-missing") is not None]
        assert with_missing, "premise: at least one stored entry names a missing item"
        assert all(not _inside_details(n.find("tenure-review-missing")) for n in with_missing)


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
