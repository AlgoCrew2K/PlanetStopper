"""Operator-facing copy names the Retirement control by its real label, and the tenure chip tells the truth.

Three findings, three groups:

* The Retirement tab's run button is labelled "Run recommender". Three places still told the operator to
  use "Run now": the never-run line, the market-hours hint and the Correlations note; the page script's
  status messages named it too.
* A tenure verdict chip read "no verdict: no evidence available" directly above panels (Performance,
  Composite score, Long VIX hedge) that held figures: the chip only meant that the three items the
  verdict rule uses were without figures.
* The backtest item's source line printed an API path (``POST /api/v0.1/backtest``) to the operator.

Real call path: the real ``GET /ai-advisor`` (tests/ui/_page_js_harness.render_page) read as text; the
real ``static/ai_advisor.js`` source read for string literals; the real
``advisors.tenure_review.decide_verdict`` and ``backtest_comparison`` over a Composer response
captured earlier (tests/fixtures/composer/backtest_response_v1.json) parsed by the real client parser.

Contract pinned
* No operator-visible text on the Advisor page (the header's distinct "Force run now" button aside), no
  string literal of ``static/ai_advisor.js`` and not the Correlations note says "Run now"; the never-run line, the market-hours hint and the Correlations
  note each say "Run recommender".
* When the redundancy, backtest and marginal items give no figure but another item holds figures, the
  verdict is still None but its text does not say there is no evidence. With every item without a
  figure the text is a non-empty sentence (its wording is not pinned here).
* No source line of the tenure review (every ``*_SOURCE`` text and the backtest item's own) holds an
  HTTP verb, an ``/api`` path, a version segment or a URL.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

import advisors.tenure_review as tr
import app as app_module
from advisors import composer_backtest_client as client
from tests.advisors.test_tenure_review_verdict import _fig, _item, _unavailable
from tests.ui._page_js_harness import render_page

REPO = Path(__file__).resolve().parents[2]
RUN_NOW = re.compile(r"\brun now\b", re.I)
REAL_LABEL = "Run recommender"


def _visible_text(html: str) -> str:
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<(script|style|template)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _sentences_with(text: str, needle: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if needle.lower() in s.lower()]


def test_precondition_the_run_button_is_labelled_run_recommender():
    page = render_page("/ai-advisor")
    match = re.search(r'data-testid="retirement-run-now-btn"[^>]*>(.*?)</button>', page, flags=re.S)
    assert match and " ".join(match.group(1).split()) == REAL_LABEL


class TestTheControlIsNamedByItsLabel:
    def test_no_visible_text_on_the_advisor_page_says_run_now(self):
        # The header's own "Force run now" button is a different control, on every page; the
        # Retirement copy is what must not call its button "Run now".
        page = re.sub(
            r'<button[^>]*data-testid="force-run-btn".*?</button>',
            " ",
            render_page("/ai-advisor"),
            flags=re.S,
        )
        assert "force-run-btn" in render_page("/ai-advisor"), (
            "precondition: the header's button exists"
        )
        text = _visible_text(page)
        found = [m.start() for m in RUN_NOW.finditer(text)]
        assert not found, [text[max(0, i - 60) : i + 60] for i in found]

    def test_the_never_run_line_names_the_real_button(self):
        text = _visible_text(render_page("/ai-advisor"))
        # The line itself opens with "Not run yet"; another sentence of the page may say a lookup "has not
        # run yet" (the model note) and is not this line.
        lines = [s for s in _sentences_with(text, "Not run yet") if s.startswith("Not run yet")]
        assert lines, "precondition: a page with no run shows the never-run line"
        assert all(REAL_LABEL in line for line in lines), lines

    def test_the_market_hours_hint_names_the_real_button(self):
        text = _visible_text(render_page("/ai-advisor"))
        hints = _sentences_with(text, "outside market hours")
        assert hints, "precondition: the page shows the market-hours hint"
        assert all(REAL_LABEL in hint and not RUN_NOW.search(hint) for hint in hints), hints

    def test_the_correlations_note_names_the_real_button(self):
        note = app_module._CORRELATION_NEXT_RUN_TEXT
        assert REAL_LABEL in note and not RUN_NOW.search(note), note

    def test_no_string_literal_of_the_page_script_says_run_now(self):
        source = (REPO / "static" / "ai_advisor.js").read_text(encoding="utf-8")
        source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
        source = re.sub(r"(^|[^:\\'\"])//[^\n]*", r"\1", source)
        literals = re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", source)
        said = [a or b for a, b in literals if RUN_NOW.search(a or b)]
        assert not said, said


# ---------------------------------------------------------------------------
# The tenure chip
# ---------------------------------------------------------------------------


def _evidence(**overrides):
    base = {
        "performance": _item(figures={"total_return": _fig(12.0)}),
        "composite": _item(figures={"composite": _fig(0.4, "score")}),
        "redundancy": _unavailable(pair=None, retire_candidate=False),
        "backtest": _unavailable("Composer unavailable"),
        "marginal": _unavailable(),
        "long_vix": _item(figures={"held_days": _fig(30, "days")}),
    }
    base.update(overrides)
    return base


class TestTheChipDoesNotContradictThePanelsBelowIt:
    def test_with_the_three_verdict_items_empty_but_other_panels_holding_figures_the_chip_claims_no_lack_of_evidence(
        self,
    ):
        got = tr.decide_verdict(_evidence())
        assert got["verdict"] is None, "precondition: no verdict from the three items"
        text = got["verdict_text"]
        assert text and text.strip(), "the chip has no text"
        assert not re.search(r"no evidence", text, re.I), (
            f"the chip reads {text!r} above panels that hold evidence"
        )

    @pytest.mark.parametrize("holding", ["performance", "composite", "long_vix"])
    def test_one_panel_holding_figures_is_enough(self, holding):
        evidence = _evidence(
            performance=_unavailable("no history"),
            composite=_unavailable("no history"),
            long_vix=_unavailable("no history"),
        )
        evidence[holding] = _item(figures={"x": _fig(1.0)})
        text = tr.decide_verdict(evidence)["verdict_text"]
        assert not re.search(r"no evidence", text, re.I), (holding, text)

    def test_with_every_item_empty_the_chip_still_says_something(self):
        evidence = {name: _unavailable("no history") for name in tr.EVIDENCE_ITEM_ORDER}
        got = tr.decide_verdict(evidence)
        assert got["verdict"] is None
        assert got["verdict_text"] and got["verdict_text"].strip()

    def test_the_items_without_figures_are_still_listed_as_missing(self):
        got = tr.decide_verdict(_evidence())
        assert got["missing_items"] == ["redundancy", "backtest", "marginal"]


# ---------------------------------------------------------------------------
# No API path in an operator-facing source line
# ---------------------------------------------------------------------------

API_TEXT = re.compile(r"\b(GET|POST|PUT|DELETE)\b|/api\b|/v\d+(\.\d+)?\b|https?://", re.I)
_FIXTURE = REPO / "tests" / "fixtures" / "composer" / "backtest_response_v1.json"


def test_no_source_line_the_review_can_print_holds_an_api_path():
    sources = {
        name: value
        for name, value in vars(tr).items()
        if name.endswith("_SOURCE") and isinstance(value, str)
    }
    assert sources, "precondition: the module names its source lines"
    for name, value in sources.items():
        assert not API_TEXT.search(value), f"{name} reads {value!r}"


def test_the_backtest_items_source_line_holds_no_api_path_whether_it_is_available_or_not():
    captured = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    parsed = client._parse_response(captured, captured["_symphony_id"])  # noqa: SLF001
    result = dataclasses.replace(parsed, last_market_day=max(parsed.daily_portfolio_values))
    dates = sorted(result.daily_portfolio_values)
    window = {"first": dates[0], "last": dates[-1], "days": len(dates)}
    available = tr.backtest_comparison(
        window=window, realized_total_return=5.0, realized_max_drawdown=-3.0, backtest=result
    )
    unavailable = tr.backtest_comparison(
        window=window, realized_total_return=5.0, realized_max_drawdown=-3.0, backtest=None
    )
    assert available["status"] == "available"
    for item in (available, unavailable):
        assert item["source"] and not API_TEXT.search(item["source"]), item["source"]
