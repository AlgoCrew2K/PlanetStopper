"""
RED/regression tests — AC-4c honest MAX-DD framing (no misleading +0.00% guard win).

DATA-CORRECTNESS-AUDIT F5/MDD: the hero MAX DD Bot +0.00% is an empty/insufficient-series
artifact (a flat divergence series has zero peak-to-trough), but it renders beside Held
+24.47% as if the bot had ZERO drawdown — reading as a guard win when it is just "not enough
history yet."

THE CONTRACT (AC-4c): when the history is insufficient (<30 trading days, the Bailey/de-Prado
2014 interpretability floor) the rendered MAX DD row must annotate "insufficient history",
SUPPRESS the misleading alpha delta, and NOT mark the Bot as the drawdown "winner"; when
history IS sufficient the comparison renders normally (the suppression is scoped, not blanket).

Two layers, each able to fail on a wrong implementation:
  - TEMPLATE-SOURCE contract: the Max DD row gates the insufficient annotation, the alpha
    delta, and the winner styling on the ``mdd_insufficient`` flag. Deterministic; fails if a
    refactor drops a guard.
  - RENDERED-PAGE: the real dashboard rendered with an insufficient-history strip shows the
    annotation and no winner styling on the MDD row. Confirms the gates fire end to end.

The PM owns the LIVE MDD gate.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_INDEX_HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"


def _template_src() -> str:
    return _INDEX_HTML_PATH.read_text(encoding="utf-8")


def _max_dd_row_markup(src: str) -> str:
    """The Max DD <div class="vs-row"> markup block — the rendered row containing the
    comp-mdd-* spans and the data-row="mdd" bars. Scoped to the MDD row only so the
    today/cumulative rows' (correctly different) winner gating is not in scope."""
    anchor = src.find('data-testid="comp-mdd-bot-text"')
    assert anchor != -1, "template must render a comp-mdd-bot-text span"
    # Open: back up to the enclosing vs-row div; Close: the data-row="mdd" bars end before
    # the next vs-row. Use the comp-mdd anchor +/- a window that covers the row + its bars.
    start = src.rfind('<div class="vs-row"', 0, anchor)
    if start == -1:
        start = max(0, anchor - 400)
    # The MDD bars carry data-row="mdd"; the row ends before the Ann Vol vs-row label.
    end = src.find("Ann", anchor)
    if end == -1:
        end = anchor + 2500
    return src[start:end]


# ===========================================================================
# TEMPLATE-SOURCE contract — the honest-framing gates exist on the MDD row
# ===========================================================================


class TestMddTemplateGatesOnInsufficiency:
    def test_template_defines_mdd_insufficient_flag(self):
        src = _template_src()
        assert "mdd_insufficient" in src, (
            "AC-4c FAIL: the template does not derive an `mdd_insufficient` flag from "
            "meta.portfolio.insufficient_history — it cannot framing-gate the 0.00% artifact."
        )

    def test_insufficient_label_is_gated_on_the_flag(self):
        block = _max_dd_row_markup(_template_src())
        assert re.search(
            r"{%\s*if\s+mdd_insufficient\s*%}[^\n]*mdd-insufficient-label",
            block,
        ), (
            "AC-4c FAIL: the 'mdd-insufficient-label' annotation is not gated on "
            "`{% if mdd_insufficient %}` — it must show ONLY when history is insufficient."
        )

    def test_alpha_delta_is_suppressed_when_insufficient(self):
        block = _max_dd_row_markup(_template_src())
        assert re.search(
            r"{%\s*if\s+not\s+mdd_insufficient\s*%}.*?comp-mdd-delta",
            block,
            re.DOTALL,
        ), (
            "AC-4c FAIL: the comp-mdd-delta alpha span is not gated on "
            "`{% if not mdd_insufficient %}` — a misleading 0.00% guard-saved alpha would "
            "render under insufficient history."
        )

    def test_mdd_winner_styling_is_gated_on_sufficiency(self):
        """Every 'winner' styling in the MDD ROW markup (the data-row='mdd' bars) must be
        guarded by `not mdd_insufficient`. Scoped to the MDD row — the today/cumulative rows
        legitimately gate on tc_bot_wins/cr_bot_wins, not on sufficiency."""
        block = _max_dd_row_markup(_template_src())
        # Only consider 'winner' tokens that live in attribute markup (a class gate), not
        # the explanatory comment prose.
        for m in re.finditer(r"%}winner", block):
            preceding = block[max(0, m.start() - 200) : m.start()]
            assert "not mdd_insufficient" in preceding, (
                "AC-4c FAIL: a 'winner' styling in the Max DD row is not guarded by "
                "`not mdd_insufficient` — a Bot +0.00% empty-series artifact could be "
                f"marked the drawdown winner. Context: ...{preceding[-120:]!r}"
            )


# ===========================================================================
# RENDERED-PAGE — the gates fire end to end on an insufficient-history page
# ===========================================================================


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_triggers.return_value = []
        db_mock.get_guard_alpha_by_symphony.return_value = {}
        yield db_mock


def _analytics_mock_insufficient():
    """A short (<30d) shadow history so the route's sufficiency gate fires; Bot MDD 0.0 /
    Held 24.47 — the exact misleading pair from the audit."""
    short_dates = [f"2026-05-{d:02d}" for d in range(18, 27)]  # 9 days
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 0.0, "dry_run": 0.0}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 10.0}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 24.47, "dry_run": 0.0}
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 12.0, "dry_run": 12.0}
    m.get_symphony_max_drawdown.return_value = {"if_held": 8.0, "dry_run": 0.0}
    m.get_portfolio_daily_returns_from_shadow.return_value = (short_dates, [0.0] * 9)
    m.compute_portfolio_annualized_vol.return_value = None
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = (short_dates, [0.0] * 9, [0.0] * 9)
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


class TestMddRenderedInsufficientFraming:
    def test_rendered_insufficient_page_shows_annotation_no_winner(
        self, client, mock_database, monkeypatch
    ):
        mock_database.load_state.return_value = {
            "sym-x": {
                "name": "Symphony X",
                "account": "ACC1",
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
                "current_return": 1.5,
                "current_value": 10000.0,
                "stop_trigger": -2.0,
                "mc_prob": 40.0,
                "simple_return": 0.12,
                "net_deposits": 1000.0,
                "time_weighted_return": 0.12,
                "max_drawdown": 0.08,
            }
        }
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "analytics", _analytics_mock_insufficient())

        resp = client.get("/")
        assert resp.status_code == 200, f"dashboard render failed: {resp.status_code}"
        html = resp.get_data(as_text=True)

        # If history is insufficient, the annotation shows. (If the route assembled >=30
        # days from another source, the test would see no annotation — which would be a
        # legitimate signal that the sufficiency wiring changed, not a flake.)
        anchor = html.find('data-testid="comp-mdd-bot-text"')
        assert anchor != -1, "rendered page must contain the Max DD row"
        # Scope strictly to the MDD row: from its label to the NEXT vs-row container
        # (the Ann Vol row). Use the data-row="mdd" bars' end — the next 'data-testid="vs-row"'
        # after the MDD anchor opens the vol row.
        row_start = html.rfind("Max DD", 0, anchor)
        next_vs_row = html.find('data-testid="vs-row"', anchor)
        row_end = next_vs_row if next_vs_row != -1 else anchor + 1500
        mdd_row = html[row_start:row_end]
        # Strip HTML comments so explanatory prose ("INVERTED winner direction" in the next
        # row's leading comment) is not mistaken for winner STYLING.
        mdd_row_nocomment = re.sub(r"<!--.*?-->", "", mdd_row, flags=re.DOTALL)
        if 'data-testid="mdd-insufficient-label"' in mdd_row_nocomment:
            # A winner is only a guard win if it appears as a CSS class on a rendered element.
            assert not re.search(r'class="[^"]*\bwinner\b[^"]*"', mdd_row_nocomment), (
                "AC-4c FAIL: rendered Max DD row applies a 'winner' class while showing the "
                "insufficient-history annotation — the 0.00% artifact is presented as a win."
            )
            assert 'data-testid="comp-mdd-delta"' not in mdd_row, (
                "AC-4c FAIL: rendered Max DD row shows the alpha delta under insufficient "
                "history — the misleading guard-saved number must be suppressed."
            )
        else:
            pytest.fail(
                "AC-4c expected insufficient-history framing on a 9-day shadow page but the "
                "annotation was absent — the route did not gate the MDD row on sufficiency."
            )
