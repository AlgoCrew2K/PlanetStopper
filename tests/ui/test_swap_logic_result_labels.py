"""The labels of a swap or logic-change result the page scripts write, read under node.

Subject: the real ``static/ai_advisor_asset_swaps.js`` and ``static/ai_advisor_logic_changes.js`` over
the real ``/ai-advisor`` page (``tests/ui/_page_js_harness.py``), answered with the body the real
route returned (``tests/ui/_swap_logic_answers.py``). The layout of the result is measured in a
browser by ``tests/ui/test_swap_logic_result_alignment_browser.py``; this module pins what needs no
layout and so also runs where browsers are deselected.

Contract pinned (the plan's AC-4 for the Asset Swaps and Logic Changes tabs, and AC-7):

* The Baseline and the Variant label of every result card name their Glossary line through
  ``data-glossary`` (W06 on the Asset Swaps tab, L05 on the Logic Changes tab), because a label
  the page script writes has no other way to reach the line that defines it.
* The figures a card prints are unchanged: the rows and their labels are the ones the route's body
  gives, in the order the script has always written them.
"""

from __future__ import annotations

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy_fixture
from tests.ui import _swap_logic_answers as answers
from tests.ui._page_explainers_advisor import soup
from tests.ui._page_js_harness import answer, render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

SWAP = {
    "host": "Asset Swaps",
    "key": "W06",
    "url": r"/ai-advisor/asset-swaps/evaluate$",
    "setup": "H.$('#swap-symphony-select').innerHTML = '<option value=\"sym-x\" selected>x</option>';"
    " H.$('#swap-evaluate-btn').removeAttribute('disabled');",
    "click": "await H.click('#swap-evaluate-btn');",
    "results": "#swap-results-area",
    "labels": ["Sharpe", "Sortino", "Max DD"],
}
LOGIC = {
    "host": "Logic Changes",
    "key": "L05",
    "url": r"/ai-advisor/logic-changes/evaluate$",
    "setup": "H.$('#tweak-symphony-select').innerHTML = '<option value=\"sym-x\" selected>x</option>';"
    " H.$('#tweak-description').value = 'raise the threshold';"
    " H.$('#tweak-evaluate-btn').removeAttribute('disabled');",
    "click": "await H.click('#tweak-evaluate-btn');",
    "results": "#logic-results-area",
    "labels": ["Sharpe", "Sortino", "Max DD", "Ann. return"],
}
CASES = {"swap": SWAP, "logic": LOGIC}


def _results_html(case: dict, response) -> str:
    run = run_page(
        render_page("/ai-advisor"),
        "await H.boot();\n"
        f"{{ {case['setup']} }}\n"
        f"{case['click']}\n"
        "await H.advance(100);\n"
        f"return H.$('{case['results']}').innerHTML;",
        routes=[route(case["url"], answer(response), method="POST")],
        pathname="/ai-advisor",
    )
    assert len(run.posts(case["url"])) == 1, "Evaluate did not send exactly one request"
    assert isinstance(run.result, str) and run.result.strip(), "the script wrote no result"
    return run.result


def _response(name: str, monkeypatch):
    return (
        answers.swap_response(monkeypatch)
        if name == "swap"
        else answers.logic_response(monkeypatch)
    )


@pytest.mark.parametrize("name", list(CASES))
def test_the_baseline_and_variant_labels_of_every_result_card_name_their_glossary_line(
    name, monkeypatch
):
    case = CASES[name]
    page = soup(_results_html(case, _response(name, monkeypatch)))
    columns = page.select(".stats-col-label")
    assert columns, "premise: a result card prints a Baseline and a Variant column"
    assert [c.get_text(strip=True) for c in columns][:2] == ["Baseline", "Variant"]
    problems = [
        f"{c.get_text(strip=True)!r} carries data-glossary {c.get('data-glossary')!r}, not {case['key']}"
        for c in columns
        if c.get("data-glossary") != case["key"]
    ]
    assert not problems, "\n".join(problems)
    # The line the key names is a line of that tab's Glossary, so the title has something to say.
    assert [line for line in copy_fixture.glossary(case["host"]) if line["key"] == case["key"]]


def _logic_results_with_guidance(monkeypatch, guidance, *, drop: bool = False) -> list:
    """The cards the Logic Changes script writes over the real route's body with every card's apply
    guidance replaced (the one named edit), or the key removed when ``drop``."""
    case = LOGIC
    response = answers.logic_response(monkeypatch)
    spec = answer(response)
    cards = list(spec["json"].get("survivors_detail") or []) + list(
        spec["json"].get("rejected_detail") or []
    )
    assert cards, "premise: the real route returned a card"
    for card in cards:
        if drop:
            card.pop("apply_guidance", None)
        else:
            card["apply_guidance"] = guidance
    run = run_page(
        render_page("/ai-advisor"),
        "await H.boot();\n"
        f"{{ {case['setup']} }}\n"
        f"{case['click']}\n"
        "await H.advance(100);\n"
        f"return H.$('{case['results']}').innerHTML;",
        routes=[route(case["url"], spec, method="POST")],
        pathname="/ai-advisor",
    )
    return soup(run.result).select(".sym-card")


def _apply_text(card) -> str | None:
    node = card.select_one('[data-testid="apply-guidance"]')
    return " ".join(node.get_text(" ").split()) if node is not None else None


@pytest.mark.parametrize("name", list(CASES))
def test_a_result_card_whose_guidance_begins_to_apply_says_it_once(name, monkeypatch):
    """S-3.15: the engine's own guidance begins "To apply:"; the card must not put a second label
    before it (the Logic Changes card did; the Asset Swaps card strips it, and stays so). Rejects a
    bold label printed ahead of text that already carries it."""
    response = _response(name, monkeypatch)
    cards = list(response.get_json().get("survivors_detail") or []) + list(
        response.get_json().get("rejected_detail") or []
    )
    guidance = cards[0]["apply_guidance"]
    assert guidance.startswith("To apply:"), "premise: the engine writes its own label"
    shown = _results_html(CASES[name], response)
    nodes = soup(shown).select('[data-testid="apply-guidance"], .apply-guidance')
    assert nodes, "premise: the card prints its guidance"
    rest = guidance[len("To apply:") :].strip()
    for node in nodes:
        text = " ".join(node.get_text(" ").split())
        assert text.count("To apply:") == 1, text
        assert text == f"To apply: {' '.join(rest.split())}", text


@pytest.mark.parametrize(
    "guidance",
    ["open Composer and widen the lookback", "Open Composer; to apply: widen it by hand"],
    ids=["plain guidance", "a later to apply inside the text"],
)
def test_guidance_that_does_not_begin_to_apply_gets_the_label_once_before_it(monkeypatch, guidance):
    """S-3.16: a guidance without its own label is printed after one "To apply:"."""
    cards = _logic_results_with_guidance(monkeypatch, guidance)
    assert cards
    for card in cards:
        text = _apply_text(card)
        assert text == f"To apply: {guidance}", text


@pytest.mark.parametrize("guidance", ["", None])
def test_empty_or_absent_guidance_prints_no_apply_line(monkeypatch, guidance):
    """S-3.16: no apply line at all when there is nothing to apply (as at the reference)."""
    cards = _logic_results_with_guidance(monkeypatch, guidance, drop=guidance is None)
    assert cards
    for card in cards:
        assert _apply_text(card) is None
        assert "To apply" not in card.get_text(" ")


@pytest.mark.parametrize("name", list(CASES))
def test_the_figure_rows_of_a_result_card_keep_their_labels_and_order(name, monkeypatch):
    case = CASES[name]
    page = soup(_results_html(case, _response(name, monkeypatch)))
    cards = [card for card in page.select(".sym-card") if card.select(".stat-row")]
    assert cards, "premise: a result card prints its figures"
    for card in cards:
        rows = card.select(".stat-row")
        # The Baseline column first, then the Variant column, each in the script's order.
        assert [r.select_one(".stat-label").get_text(strip=True) for r in rows] == (
            case["labels"] * 2
        ), "a figure row lost its label or moved"
        for row in rows:
            value = row.select_one(".stat-value")
            assert value is not None and value.get_text(strip=True), "a figure row prints no value"
