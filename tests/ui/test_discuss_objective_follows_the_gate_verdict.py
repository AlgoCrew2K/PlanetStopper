"""Discuss on a Strategy Builder card never says the candidate cleared the gate unless it did.

Real call path: ``static/ai_advisor.js`` and ``static/ai_advisor_chat.js`` run unmodified over the page
the real app renders (tests/ui/_page_js_harness.py). The scenario calls the page's own
``openChatWithArtifact`` (what a card's Discuss button calls) with an artifact of the shape
``app.ai_advisor_tab`` builds for a Strategy Builder card; the line under test is the one the real
chat panel then shows in ``#artifact-objective``, beside the verdict pill ``#artifact-gate-pill``.

The artifact's fields other than ``gate_verdict`` are minimal and synthetic (labelled so): only the
verdict varies between cases. The verdict names are the ones the page's pill maps: ``ADOPT_CANDIDATE``,
``KEEP_INCUMBENT`` (withheld) and ``REJECT_VETO_FAILED`` (rejected).

Contract pinned
* For a rejected candidate and for a withheld one, the objective line makes no claim that the
  candidate cleared, passed or survived the gate (a negated statement is not a claim).
* The same holds when the artifact carries no verdict.
* The line follows the verdict: the adopted, withheld and rejected lines differ from each other,
  so a constant string, or one changed only for the adopted case, fails.
* The adopted candidate's line may say it cleared the gate; the pill agrees with the artifact's
  verdict in each case.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.ui._page_js_harness import render_page, route, run_page

VERDICTS = {
    "adopted": "ADOPT_CANDIDATE",
    "withheld": "KEEP_INCUMBENT",
    "rejected": "REJECT_VETO_FAILED",
}

# Synthetic artifact in the shape app.ai_advisor_tab builds for a Strategy Builder card.
ARTIFACT = {
    "artifact_type": "strategy_proposal",
    "artifact_id": "cand-synthetic-0001",
    "symphony_id": "sym-test",
    "template_id": "built-new",
    "tickers": ["SPY", "QQQ", "TLT"],
    "rules_text": "Hold SPY above its 200-day average, else TLT.",
    "n_candidates": 12,
    "n_survivors": 1,
    "cagr": 0.1234,
    "sharpe": 0.9,
    "calmar": 1.1,
}

SCENARIO = (
    r"""
await H.boot();
const artifact = __ARTIFACT__;
const out = {};
for (const key of Object.keys(__VERDICTS__)) {
  const a = Object.assign({}, artifact, { gate_verdict: __VERDICTS__[key] });
  H.win('openChatWithArtifact')(JSON.stringify(a));
  await H.settle();
  out[key] = { objective: H.text('#artifact-objective'), pill: H.text('#artifact-gate-pill') };
}
const none = Object.assign({}, artifact);
delete none.gate_verdict;
H.win('openChatWithArtifact')(JSON.stringify(none));
await H.settle();
out.missing = { objective: H.text('#artifact-objective'), pill: H.text('#artifact-gate-pill') };
return out;
""".replace("__ARTIFACT__", json.dumps(ARTIFACT)).replace("__VERDICTS__", json.dumps(VERDICTS))
)

CLAIM = re.compile(r"\b(cleared|passed|survived)\b", re.I)
NEGATION = re.compile(
    r"\b(not|never|didn't|did not|failed to|fail to|fails to|without|unable to)\b(\s+\w+){0,2}\s+"
    r"(clear|cleared|pass|passed|survive|survived)\b",
    re.I,
)


def claims_to_have_cleared(text: str) -> bool:
    """True when the text says the candidate cleared, passed or survived, with no negation of it."""
    if not CLAIM.search(text):
        return False
    return not NEGATION.search(text)


@pytest.fixture(scope="module")
def lines():
    run = run_page(
        render_page("/ai-advisor"),
        SCENARIO,
        routes=[route(r"/api/autotune-runs$", {"json": []}, method="GET")],
        pathname="/ai-advisor",
    )
    return run.result


def test_precondition_the_panel_shows_the_verdict_pill_for_each_case(lines):
    assert lines["adopted"]["pill"] == "passed"
    assert lines["withheld"]["pill"] == "withheld"
    assert lines["rejected"]["pill"] == "rejected"


def test_the_claim_detector_tells_a_claim_from_its_negation():
    assert claims_to_have_cleared("Why this candidate cleared the statistical gate")
    assert not claims_to_have_cleared("Why this candidate did not clear the statistical gate")
    assert not claims_to_have_cleared("Why this candidate was not cleared by the gate")
    assert not claims_to_have_cleared("Why this candidate failed to pass the statistical gate")
    assert not claims_to_have_cleared("What the gate said about this candidate")


@pytest.mark.parametrize("case", ["rejected", "withheld", "missing"])
def test_a_candidate_that_did_not_clear_the_gate_is_never_said_to_have_cleared_it(lines, case):
    text = lines[case]["objective"]
    assert text and text.strip() not in ("", "—"), f"{case}: the panel has no objective line"
    assert not claims_to_have_cleared(text), f"{case}: the objective line reads {text!r}"


def test_the_objective_line_follows_the_verdict(lines):
    texts = {k: lines[k]["objective"] for k in ("adopted", "withheld", "rejected")}
    assert len(set(texts.values())) == 3, (
        f"the adopted, withheld and rejected candidates share objective wording: {texts}"
    )


def test_the_rejected_line_is_not_the_adopted_line_with_a_word_changed_only_for_adopted(lines):
    # A fix that rewrites only the adopted case leaves the old claim for the others.
    old_claim = "Why this candidate cleared the statistical gate"
    for case in ("withheld", "rejected", "missing"):
        assert lines[case]["objective"] != old_claim, f"{case} still carries the old fixed line"
