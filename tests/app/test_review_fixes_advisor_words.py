"""Advisor words on screen (plan 06 AC-6 and AC-7), read from the server render.

Every page here is the real ``GET /ai-advisor`` over a temp database seeded through the real writers
(observation rows through ``database.insert_advisor_observation``, the pending Frontrunner
proposal through its writer, the tenure review and the recommender run through the tab world
builders). Expected strings are derived from what each test stored; no figure is copied from a
capture.
"""

from __future__ import annotations

import re

import pytest

import database
from tests.advisors.test_retirement_recommender_composer_source import _series_cluster_scenario
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb
from tests.ui._shell_support import seed_pending_frontrunner_proposal

UPPER_SNAKE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")
LOWER_SNAKE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    yield app_module.app.test_client()


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "review_fixes.db"))
    database.init_db()
    database.run_migrations()
    return database.DB_FILE


def _page(client) -> pb.Node:
    return pb.parse_html(w.get_page(client))


# ---------------------------------------------------------------------------
# Raw enums read as words, through one mapping
# ---------------------------------------------------------------------------

# Verdicts the producers write today and one nobody has written yet: a mapping that covers only
# the stored values and passes the rest through raw would fail on the last one.
_VERDICTS = {
    "ASSET_SWAP": "REJECT_VETO_FAILED",
    "LOGIC_CHANGE": "REJECT_NOT_A_WINNER",
    "OVERFITTING_CONSCIENCE": "CLEAR",
    "SPEC_CRITIC": "neutral",
    "ADD_CANDIDATE": "SOME_FUTURE_VERDICT",
}


def _seed_observations() -> None:
    for role, verdict in _VERDICTS.items():
        database.insert_advisor_observation(
            advisor_role=role,
            subject_type="autotune_run",
            subject_id="enum-fixture",
            verdict=verdict,
            raw_response={},
            symphony_id="",
        )


def test_observation_verdicts_and_subjects_read_as_words(client, isolated_db):
    _seed_observations()
    section = _page(client).find("advisor-observations-section")
    assert section is not None
    text = section.text()
    assert not UPPER_SNAKE.findall(text), (
        f"raw enum in the observations: {UPPER_SNAKE.findall(text)}"
    )
    assert not LOWER_SNAKE.findall(text), (
        f"raw subject in the observations: {LOWER_SNAKE.findall(text)}"
    )
    assert "REJECT_VETO_FAILED" not in text and "autotune_run" not in text
    assert not re.search(r"\bneutral\b", text), "a lowercase raw verdict is on the line"


def test_the_same_words_are_used_on_the_producer_line_and_in_the_history_rows(client, isolated_db):
    _seed_observations()
    section = _page(client).find("advisor-observations-section")
    # The producer lines are the ``obs-producer`` rows of the stored observations. The plan puts one
    # more row after them, the Retirement pointer (its own ``data-role`` and no history rows): its
    # verdict cell is a status line and not a producer's latest verdict, so it is read apart, and
    # every other verdict cell is still compared with its producer's line.
    line: dict[str, str] = {}
    pointer_verdicts = []
    for n in section.walk():
        if n.testid != "obs-producer-verdict":
            continue
        owner = n.ancestor("obs-producer")
        if owner is not None:
            line[owner.attrs["data-role"]] = n.text()
        else:
            row = n.parent
            while row is not None and "data-role" not in row.attrs:
                row = row.parent
            assert row is not None and row.attrs.get("data-role") == "RETIREMENT", (
                f"a verdict cell sits in no producer line and is not the Retirement row: {n.text()}"
            )
            pointer_verdicts.append(n)
    assert len(pointer_verdicts) == 1, "the Retirement row is the one verdict cell outside a line"
    assert not UPPER_SNAKE.search(pointer_verdicts[0].text()), pointer_verdicts[0].text()
    rows = [r for r in section.walk() if r.testid == "obs-row"]
    assert line and rows
    assert set(line) == set(_VERDICTS), "a producer line is missing or one was added"
    for row in rows:
        verdict_cell = next(c for c in row.walk() if c.testid == "obs-verdict").text()
        assert verdict_cell in line.values() or not UPPER_SNAKE.search(verdict_cell)


# ---------------------------------------------------------------------------
# Max Drawdown is a positive magnitude labelled Max DD
# ---------------------------------------------------------------------------


def _sb_survivor(**rr) -> None:
    database.insert_advisor_observation(
        advisor_role="STRATEGY_BUILDER",
        subject_type="strategy_builder",
        subject_id="cand",
        verdict="ADOPT_CANDIDATE",
        raw_response={
            "sharpe": 1.2,
            "cagr": 0.11,
            "calmar": 0.5,
            "max_drawdown": -0.2421,
            "candidate_hash": "abc",
            "template_id": "built-new",
            **rr,
        },
        symphony_id="",
    )


def _sb_table(client) -> pb.Node:
    tree = _page(client)
    table = next(
        (n for n in tree.walk() if n.testid == "stats-table" and "Sharpe" in n.text()), None
    )
    assert table is not None, "no Strategy Builder card table"
    return table


def _row_cells(table: pb.Node, label: str) -> list[str] | None:
    for tr in (n for n in table.walk() if n.tag == "tr"):
        cells = [c.text() for c in tr.children if isinstance(c, pb.Node) and c.tag in ("td", "th")]
        if cells and cells[0] == label:
            return cells
    return None


def test_a_strategy_builder_card_labels_the_row_max_dd_and_prints_a_magnitude(client, isolated_db):
    _sb_survivor()
    table = _sb_table(client)
    assert _row_cells(table, "Max Drawdown") is None, "the old label is still on the card"
    cells = _row_cells(table, "Max DD")
    assert cells is not None, "no Max DD row"
    assert cells[1] == "24.21%", f"expected the magnitude of -0.2421, got {cells[1]!r}"


def test_a_strategy_builder_card_omits_the_sortino_row_when_it_has_no_value(client, isolated_db):
    _sb_survivor()
    table = _sb_table(client)
    assert _row_cells(table, "Sortino") is None, "a dash-only Sortino row is on the card"


def test_a_strategy_builder_card_keeps_the_sortino_row_when_it_has_a_value(client, isolated_db):
    _sb_survivor(metrics={"sortino_ratio": 1.2345})
    # The card reads the ratio from raw_response.metrics (templates/ai_advisor.html: metrics.get).
    table = _sb_table(client)
    cells = _row_cells(table, "Sortino")
    assert cells is not None and cells[1] == "1.234", cells


def test_the_frontrunner_card_prints_max_dd_legs_as_magnitudes(client, isolated_db):
    seed_pending_frontrunner_proposal()
    tree = _page(client)
    card = next(n for n in tree.walk() if n.testid == "frontrunner-proposal-card")
    legs = {
        n.testid: n.text()
        for n in card.walk()
        if n.testid in ("fr-incumbent-mdd", "fr-candidate-mdd")
    }
    assert set(legs) == {"fr-incumbent-mdd", "fr-candidate-mdd"}
    for testid, text in legs.items():
        assert not re.search(r"[-−]", text), f"{testid} shows a signed drawdown: {text!r}"
    labels = [n.text() for n in card.walk() if n.tag == "td"]
    assert "Max DD" in labels and "Max Drawdown" not in labels


def test_every_template_source_of_a_drawdown_prints_the_magnitude():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "templates" / "ai_advisor.html").read_text(
        encoding="utf-8"
    )
    assert "<td>Max Drawdown</td>" not in source, "a card still labels the row Max Drawdown"
    unsigned = [
        m.group(0)
        for m in re.finditer(r'"%\.2f%%"\s*\|\s*format\([^)]*mdd[^)]*\)', source)
        if "abs" not in m.group(0)
    ]
    assert unsigned == [], f"drawdown formatted without abs(): {unsigned}"


@pytest.mark.parametrize("value", [-27.07, 0.0])  # percent scale, as the tenure review stores it
def test_tenure_evidence_prints_max_drawdown_as_a_magnitude(client, isolated_db, value):
    record = pb.full_record()
    entry = pb.entry_with_verdict(record, "watch")
    entry["evidence"]["performance"]["figures"]["max_drawdown"]["value"] = value
    pb.store_run(record)
    block = pb.entry_node(pb.panel_of(w.get_page(client)), entry["symphony_id"]).find(
        "tenure-review-evidence-performance"
    )
    lines = [
        n.text()
        for n in pb.leaf_nodes_containing(block, "max drawdown")
        if n.text().lower().startswith("max drawdown:")
    ]
    assert len(lines) == 1, lines
    assert not re.search(r"[-−]", lines[0]), (
        f"a signed drawdown in the tenure evidence: {lines[0]!r}"
    )
    assert f"{abs(value):.2f}" in lines[0], lines[0]


# Every drawdown figure of the tenure evidence is a positive magnitude, the realized and backtest
# legs and the with and without legs included, and a gap between two of them reads correctly in that
# convention. The stored values stay signed (a drawdown is at most zero, and the verdict compares the
# signed values), so the edit below is the render's only input: the stored figures are set the way
# the producer computes them (the first leg minus the second, in percent, signed).
_DRAWDOWN_LEGS = {
    "backtest": {
        "first": "realized_max_drawdown",
        "second": "backtest_max_drawdown",
        "gap": "max_drawdown_gap",
        "subjects": ("realized", "backtest"),
    },
    "marginal": {
        "first": "without_max_drawdown",
        "second": "with_max_drawdown",
        "gap": "max_drawdown_difference",
        "subjects": ("without", "with"),
    },
}
_LEG_PAIRS = [(-12.35, -15.50), (-20.00, -15.00), (-9.10, -9.10), (0.0, -4.25)]


def _drawdown_lines(client, item: str, signed: dict[str, float]) -> dict[str, str]:
    record = pb.full_record()
    entry = pb.entry_with_verdict(record, "watch")
    figures = entry["evidence"][item]["figures"]
    for name, value in signed.items():
        figures[name]["value"] = value
    pb.store_run(record)
    block = pb.entry_node(pb.panel_of(w.get_page(client)), entry["symphony_id"]).find(
        f"tenure-review-evidence-{item}"
    )
    lines = {}
    for node in pb.leaf_nodes_containing(block, "max drawdown"):
        text = node.text().strip()
        label = text.split(":", 1)[0].strip().lower().replace(" ", "_")
        lines[label] = text
    return lines


def _number_after_the_label(line: str) -> tuple[float, bool]:
    """The first number after the label, and whether it carries a minus sign."""
    value_part = line.split(":", 1)[1]
    match = re.search(r"([-\u2212]?)\s*(\d+\.\d{2})", value_part)
    assert match, f"no figure in {line!r}"
    return float(match.group(2)), bool(match.group(1))


@pytest.mark.parametrize("item", sorted(_DRAWDOWN_LEGS))
@pytest.mark.parametrize(("first", "second"), _LEG_PAIRS)
def test_tenure_drawdown_legs_print_positive_magnitudes(client, isolated_db, item, first, second):
    names = _DRAWDOWN_LEGS[item]
    lines = _drawdown_lines(
        client,
        item,
        {names["first"]: first, names["second"]: second, names["gap"]: first - second},
    )
    for name, value in ((names["first"], first), (names["second"], second)):
        line = lines.get(name)
        assert line, f"no {name} line among {sorted(lines)}"
        assert not re.search(r"[-\u2212]", line), (
            f"a signed drawdown in the tenure evidence: {line!r}"
        )
        assert f"{abs(value):.2f}" in line, line


@pytest.mark.parametrize("item", sorted(_DRAWDOWN_LEGS))
@pytest.mark.parametrize(("first", "second"), _LEG_PAIRS)
def test_tenure_drawdown_gap_reads_correctly_in_the_magnitude_convention(
    client, isolated_db, item, first, second
):
    """The gap is read from the magnitudes on the page: the first leg's magnitude minus the second's.
    It reads either as that signed number, or in words naming which leg is shallower or deeper."""
    names = _DRAWDOWN_LEGS[item]
    lines = _drawdown_lines(
        client,
        item,
        {names["first"]: first, names["second"]: second, names["gap"]: first - second},
    )
    line = lines.get(names["gap"])
    assert line, f"no {names['gap']} line among {sorted(lines)}"
    shown_difference = round(abs(first) - abs(second), 2)
    magnitude, minus = _number_after_the_label(line)
    assert magnitude == abs(shown_difference), (
        f"the gap {line!r} is not the difference of the magnitudes {abs(first)} and {abs(second)}"
    )
    words = line.split(":", 1)[1].lower()
    if "shallower" in words or "deeper" in words:
        first_subject, second_subject = names["subjects"]
        spots = {
            subject: m.start()
            for subject in names["subjects"]
            if (m := re.search(rf"\b{subject}\b", words))
        }
        assert spots, f"a gap in words must name which leg it speaks of: {line!r}"
        named_first = min(spots, key=spots.get) == first_subject
        word = "shallower" if "shallower" in words else "deeper"
        first_is_shallower = shown_difference < 0
        says_first_is_shallower = (word == "shallower") == named_first
        if shown_difference != 0:
            assert says_first_is_shallower == first_is_shallower, line
    elif shown_difference < 0:
        assert minus, (
            f"the {names['subjects'][0]} leg is shallower, so the gap is negative: {line!r}"
        )
    else:
        assert not minus, f"the gap should not be negative: {line!r}"


def test_the_stored_drawdown_figures_stay_signed():
    """The render takes the magnitude; the producer's figures are what the verdict compares."""
    record = pb.full_record()
    for entry in record["entries"]:
        for item in ("performance", "backtest", "marginal"):
            figures = (entry["evidence"].get(item) or {}).get("figures") or {}
            for name, figure in figures.items():
                value = figure.get("value")
                if name.endswith("max_drawdown") and isinstance(value, (int, float)):
                    assert value <= 0, f"{name} is stored as a magnitude: {value}"


# ---------------------------------------------------------------------------
# Retirement tab (UXR-21)
# ---------------------------------------------------------------------------


def _own_section(button: pb.Node) -> pb.Node:
    node = button.parent
    while node is not None and not (
        node.tag == "section" or "hero-section" in node.classes() or "matrix-card" in node.classes()
    ):
        node = node.parent
    assert node is not None, "the button is in no section"
    return node


def test_the_two_run_buttons_are_labelled_and_sit_in_their_own_sections(client, isolated_db):
    pb.store_run(pb.full_record())
    w.store_captured_summary()
    tree = _page(client)
    recommender = next(n for n in tree.walk() if n.testid == "retirement-run-now-btn")
    tenure = next(n for n in tree.walk() if n.testid == "tenure-review-run-btn")
    assert recommender.text() == "Run recommender", recommender.text()
    assert tenure.text() == "Run tenure review", tenure.text()
    first, second = _own_section(recommender), _own_section(tenure)
    assert first is not second, "both run buttons share one section"
    for section, button in ((first, recommender), (second, tenure)):
        headers = [n for n in section.walk() if "section-header" in n.classes()]
        assert headers, f"the section of {button.text()!r} has no heading"
        other = tenure if button is recommender else recommender
        assert not other.is_inside(section), "a section holds both run buttons"


def test_the_basis_line_says_composer_realized_daily_returns_with_no_internal_word(
    client, isolated_db
):
    w.store_captured_summary()
    tree = _page(client)
    basis = next(n for n in tree.walk() if n.testid == "retirement-last-run-basis")
    assert "Composer realized daily returns" in basis.text(), basis.text()
    assert not re.search(r"\bcomposer\b", basis.text()), "the source word 'composer' is on the line"


def test_the_actionable_card_comes_before_the_review_and_last_run_blocks(
    client, isolated_db, monkeypatch
):
    pb.store_run(pb.full_record())
    w.run_tick_with_recommendations(monkeypatch, _series_cluster_scenario())
    tree = _page(client)
    order = {id(n): i for i, n in enumerate(tree.walk())}

    def first(testid):
        return next((order[id(n)] for n in tree.walk() if n.testid == testid), None)

    card = first("retirement-recommendation-card")
    assert card is not None, "the tick wrote no recommendation card"
    for blocked in ("retirement-last-run", "tenure-review-entry"):
        at = first(blocked)
        if at is not None:
            assert card < at, f"the card to act on comes after {blocked}"
    status = next(n for n in tree.walk() if n.testid == "retirement-rec-status")
    header = status.parent
    while (
        header is not None
        and "card-top" not in header.classes()
        and "card-header-row" not in header.classes()
    ):
        header = header.parent
    assert header is not None, "the status pill is not in the card's header row"
