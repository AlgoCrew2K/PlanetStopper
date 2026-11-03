"""The AI Advisor Overview's order: what the operator acts on first, the Market Prism read
below it.

Subject: ``GET /ai-advisor`` over a real temp database.

* In the Overview panel the top panel (the grid) holds, in this order: the Retirement pointer,
  the controls bar, the summary strip, the lens stamp, the suggestions column and the autotune
  runs; then come the Market Prism block and the advisor observations. The pointer, the controls
  and the one-line hint share one toolbar row.
* The order is the same with a Market Prism row and without one (the empty state sits where the
  block does).
* The Prism block itself is moved, not edited: its template text equals the copy captured
  before it moved (``tests/fixtures/ai_advisor/ux_overhaul_02/prism_block_source_baseline.txt``),
  and with a stored row its lenses, sources and verification parts render inside it.

Fixture provenance: ``tests/fixtures/ai_advisor/ux_overhaul_02/PROVENANCE.md`` (Prism block records).
"""

from __future__ import annotations

import pytest

import database
from tests._advisor_observation_records import store
from tests._advisor_overview_prism import BASELINE, TEMPLATE, prism_block_source, store_prism_row
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb

_OVERVIEW_ORDER = (
    "advisor-grid",
    "retirement-pointer",
    "advisor-controls",
    "advisor-summary-strip",
    "advisor-lens-as-of",
    "suggestions-column",
    "autotune-panel",
    "market-prism-block",
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "overview_order.db"))
    database.init_db()
    database.run_migrations()
    return database.DB_FILE


def _overview(client) -> tuple[pb.Node, pb.Node, list]:
    root = pb.parse_html(w.get_page(client))
    panel = root.find("tab-panel-overview")
    assert panel is not None
    return root, panel, list(root.walk())


class TestOverviewOrder:
    @pytest.mark.parametrize("with_prism_row", [False, True], ids=["no prism row", "prism row"])
    def test_the_blocks_come_in_the_operators_order(self, isolated_db, client, with_prism_row):
        w.seed_roster()
        store("guardrail_clear", times=3)
        if with_prism_row:
            store_prism_row()
        _, panel, order = _overview(client)
        nodes = [panel.find(testid) for testid in _OVERVIEW_ORDER]
        assert None not in nodes, [
            t for t, n in zip(_OVERVIEW_ORDER, nodes, strict=True) if n is None
        ]
        positions = [order.index(n) for n in nodes]
        assert positions == sorted(positions), dict(zip(_OVERVIEW_ORDER, positions, strict=True))
        assert len(set(positions)) == len(positions)

    @pytest.mark.parametrize("with_prism_row", [False, True], ids=["no prism row", "prism row"])
    def test_the_observations_follow_the_prism_block_inside_the_panel(
        self, isolated_db, client, with_prism_row
    ):
        w.seed_roster()
        store("guardrail_clear", times=3)
        if with_prism_row:
            store_prism_row()
        _, panel, order = _overview(client)
        section = panel.find("advisor-observations-section")
        assert section is not None, "the Overview panel has no observations section"
        assert order.index(panel.find("market-prism-block")) < order.index(section)

    def test_the_empty_prism_state_sits_where_the_block_does(self, isolated_db, client):
        _, panel, order = _overview(client)
        block, empty = panel.find("market-prism-block"), panel.find("prism-empty-state")
        assert empty is not None and empty.is_inside(block)
        assert order.index(panel.find("advisor-grid")) < order.index(block)
        assert order.index(panel.find("advisor-grid")) < order.index(empty)

    def test_the_pointer_and_controls_precede_the_prism_block(self, isolated_db, client):
        w.seed_roster()
        store_prism_row()
        _, panel, order = _overview(client)
        block = panel.find("market-prism-block")
        assert block.find("prism-sentiment-chip") is not None, "premise: a stored read renders"
        for before in ("retirement-pointer", "advisor-controls"):
            assert order.index(panel.find(before)) < order.index(block), before


class TestPrismBlockIsMovedNotEdited:
    def test_the_block_text_equals_the_copy_captured_before_the_move(self):
        expected = BASELINE.read_text(encoding="utf-8")
        actual = prism_block_source(TEMPLATE.read_text(encoding="utf-8"))
        assert actual == expected

    def test_a_stored_read_renders_its_lenses_inside_the_block(self, isolated_db, client):
        w.seed_roster()
        store_prism_row()
        _, panel, _ = _overview(client)
        block = panel.find("market-prism-block")
        for testid in ("prism-as-of", "prism-sentiment-chip", "prism-rationale", "prism-lenses"):
            node = block.find(testid)
            assert node is not None and node.is_inside(block), testid
        lens_names = {"technicals", "sentiment", "derivatives", "macro", "fundamentals"}
        for lens in lens_names:
            assert block.find(f"prism-lens-{lens}") is not None, lens
