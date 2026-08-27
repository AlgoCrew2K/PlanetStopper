"""RED tests -- Cycle 2c AC-7: retDispatchDecision acts on the CLICKED
element, not a card-scan match on candidate_id.

feature-plans/retirement-approval-polish.md AC-7: retDispatchDecision acts
on the CLICKED element (event target / the button that fired), not a
card-scan match on candidate_id, so the clicked button always disables and
gives feedback even under a DOM/id edge case. Mirrors
frDispatchProposalAction's direct-element pattern.

Today's bug (static/ai_advisor.js:1106-1117): retDispatchDecision(action,
candidateId) receives only the STRING candidateId, then scans EVERY
'[data-testid="retirement-recommendation-card"]' on the page comparing each
one's approve/reject button's dataset.candidateId to the passed string to
find "the" card -- fragile under any duplicate-id or DOM-shape edge case,
and pointlessly indirect when the caller already HAS the concrete clicked
element (`this` inside the Jinja onclick handler).

Mechanics note: there is no runtime JS/DOM test harness in this repo
(established ruling -- see tests/app/test_retirement_review_remediation_pr139.py's
module docstring). Uses the SAME brace-depth-walk extraction pattern that
file established, generalized here to tolerate a changed parameter list
(the whole point of AC-7 is that the signature changes).

Non-regression guards: the F3 (reject-confirmation color) and F4 (status-chip
update) fixes from the PR#139 remediation round must survive this rework --
re-asserted here against the reworked function body.

Expected state: RED until static/ai_advisor.js's retDispatchDecision is
reworked to the clicked-element pattern and templates/ai_advisor.html's
onclick handlers are updated to pass the element itself.
"""

from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_JS_PATH = REPO_ROOT / "static" / "ai_advisor.js"
_HTML_PATH = REPO_ROOT / "templates" / "ai_advisor.html"

# The exact fragile pattern AC-7 removes: enumerating every card on the page
# and comparing a dataset attribute to find "the" one, instead of using the
# concrete clicked element the caller already has.
_OLD_CARD_SCAN_SELECTOR = "querySelectorAll('[data-testid=\"retirement-recommendation-card\"]')"

# The old onclick call shape (passes only the string dataset value).
_OLD_ONCLICK_APPROVE = "onclick=\"retDispatchDecision('approve', this.dataset.candidateId)\""
_OLD_ONCLICK_REJECT = "onclick=\"retDispatchDecision('reject', this.dataset.candidateId)\""


def _js_source() -> str:
    if not _JS_PATH.exists():
        pytest.fail(f"expected file not found: {_JS_PATH}")
    return _JS_PATH.read_text(encoding="utf-8")


def _html_source() -> str:
    if not _HTML_PATH.exists():
        pytest.fail(f"expected file not found: {_HTML_PATH}")
    return _HTML_PATH.read_text(encoding="utf-8")


def _extract_braced_js_block(source: str, open_brace_idx: int) -> str:
    """Brace-depth walk from `open_brace_idx` (must point at a literal '{')
    through its matching close, inclusive. Not string-literal-aware -- same
    scoping rationale as test_retirement_review_remediation_pr139.py's
    identical helper (retDispatchDecision's body has no template literals or
    stray braces inside string literals)."""
    assert source[open_brace_idx] == "{"
    depth = 0
    for i in range(open_brace_idx, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace_idx : i + 1]
    pytest.fail("_extract_braced_js_block: no matching close brace found -- malformed JS?")


def _retdispatch_function_body() -> str:
    """Locates retDispatchDecision's body by its NAME + parameter-list
    parens only -- deliberately tolerant of a changed parameter list (AC-7's
    whole point is that the signature changes from (action, candidateId) to
    something that carries the clicked element)."""
    source = _js_source()
    marker = "function retDispatchDecision("
    name_idx = source.find(marker)
    if name_idx == -1:
        pytest.fail(
            "static/ai_advisor.js: 'function retDispatchDecision(' not found -- "
            "has the function been renamed?"
        )
    paren_start = name_idx + len(marker) - 1
    assert source[paren_start] == "("
    depth = 0
    close_paren_idx = None
    for i in range(paren_start, len(source)):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                close_paren_idx = i
                break
    if close_paren_idx is None:
        pytest.fail("retDispatchDecision: could not find the end of its parameter list.")
    brace_idx = source.find("{", close_paren_idx)
    if brace_idx == -1:
        pytest.fail("retDispatchDecision: could not find its opening '{'.")
    return _extract_braced_js_block(source, brace_idx)


def _retdispatch_success_branch() -> str:
    fn_body = _retdispatch_function_body()
    marker = "if (data && data.success === true) {"
    idx = fn_body.find(marker)
    if idx == -1:
        pytest.fail(
            "retDispatchDecision: 'if (data && data.success === true) {' not found -- "
            "has the success-branch condition changed shape?"
        )
    open_brace_idx = idx + len(marker) - 1
    return _extract_braced_js_block(fn_body, open_brace_idx)


# ===========================================================================
# AC-7 core: clicked-element pattern replaces the card-scan
# ===========================================================================


class TestClickedElementPatternReplacesCardScan:
    def test_old_card_scan_selector_removed(self):
        fn_body = _retdispatch_function_body()
        assert _OLD_CARD_SCAN_SELECTOR not in fn_body, (
            "retDispatchDecision still enumerates every "
            '[data-testid="retirement-recommendation-card"] on the page and compares '
            "a dataset attribute to find the matching one -- AC-7 requires acting on "
            "the CLICKED element directly instead."
        )

    def test_uses_closest_to_derive_the_ancestor_card(self):
        fn_body = _retdispatch_function_body()
        assert ".closest(" in fn_body, (
            "Expected retDispatchDecision to derive the ancestor card via .closest(...) "
            "from the clicked element (the direct-element pattern), not a page-wide scan."
        )

    def test_template_approve_button_passes_the_clicked_element(self):
        html = _html_source()
        assert _OLD_ONCLICK_APPROVE not in html, (
            "The approve button's onclick still passes only "
            "this.dataset.candidateId (a bare string) -- AC-7 requires passing the "
            "clicked element itself (e.g. `this`) so retDispatchDecision can act on "
            "it directly."
        )
        assert "onclick=\"retDispatchDecision('approve', this)\"" in html, (
            "Expected the approve button's onclick to pass `this` (the clicked "
            "button element) to retDispatchDecision."
        )

    def test_template_reject_button_passes_the_clicked_element(self):
        html = _html_source()
        assert _OLD_ONCLICK_REJECT not in html, (
            "The reject button's onclick still passes only this.dataset.candidateId."
        )
        assert "onclick=\"retDispatchDecision('reject', this)\"" in html, (
            "Expected the reject button's onclick to pass `this` (the clicked "
            "button element) to retDispatchDecision."
        )

    def test_post_body_still_carries_candidate_id_key(self):
        """Non-regression: the wire contract to /ai-advisor/retirement/
        approve|reject is unchanged -- still a JSON body with a candidate_id
        key -- regardless of how the JS internally derives that value now."""
        fn_body = _retdispatch_function_body()
        assert "candidate_id" in fn_body, (
            "retDispatchDecision must still POST a JSON body carrying candidate_id "
            "-- the server-side route contract is unchanged by this JS-only rework."
        )

    def test_function_still_disables_a_button_before_dispatch(self):
        """Non-regression (double-submit guard): the clicked button (at
        minimum) must still be disabled synchronously before the async
        fetch, regardless of the internal variable naming this rework uses."""
        fn_body = _retdispatch_function_body()
        assert ".disabled = true" in fn_body, (
            "retDispatchDecision no longer disables any button before dispatching -- "
            "a double-submit guard must survive this rework."
        )


# ===========================================================================
# Non-regression: F3 (reject confirmation color) / F4 (status chip update)
# from the PR#139 remediation round must survive this rework
# ===========================================================================


class TestPriorRoundFixesSurviveTheRework:
    def test_success_branch_still_branches_confirmation_color_by_action(self):
        success_branch = _retdispatch_success_branch()
        assert "--studio-pos" in success_branch
        assert "--studio-neg" in success_branch, (
            "F3 regression: the reject-vs-approve confirmation color branch "
            "(--studio-neg for reject) must survive the AC-7 clicked-element rework."
        )

    def test_success_branch_still_updates_the_status_chip(self):
        success_branch = _retdispatch_success_branch()
        assert (
            "retirement-rec-status" in success_branch or "retirement-status-chip" in success_branch
        ), "F4 regression: the status-chip update must survive the AC-7 clicked-element rework."
        assert "textContent" in success_branch or "innerText" in success_branch
        assert "retirement-status-chip--" in success_branch
