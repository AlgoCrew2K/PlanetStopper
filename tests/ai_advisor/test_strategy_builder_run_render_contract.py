"""
RED tests — Strategy Builder on-demand run renders in-place (AC-1, AC-2).

Feature plan: feature-plans/advisor-suite-fixes.md.

THE BUG (ADVISOR-AUDIT-VERDICT.md, 2026-07-13): static/ai_advisor.js:749's
sbRunAnalysis() unconditionally does `window.location.href = '/ai-advisor'` on
a successful POST to /ai-advisor/strategy-builder/run — discarding the response
JSON entirely and forcing a full page navigation. The route already returns the
complete outcome (app.py:4711-4719: survivors, rejected, n_candidates,
fdr_adjusted_threshold, error) — the fix is to RENDER that response in-place,
never navigate away on success.

AC-2 (run identifiability) is a corollary of the same fix: rendering directly
from the POST's own response JSON (rather than re-fetching a limit-50 history
slice, per database.py:1205 / ai_advisor.html:1968) is what scopes the
displayed cards to THIS run. So this file tests both ACs as one contract: no
navigation, no re-fetch, render from `data` directly.

Harness note: this project has no jsdom/e2e browser runner wired into pytest —
established pattern is source-text assertions against static/*.js (see
tests/dashboard/test_render_basis_fix.py, tests/dashboard/test_card_consistency_liveness.py).
These tests pin the CLIENT WIRING CONTRACT; the PM's live-UI gate (fix-ux,
Playwright screenshots) is the authoritative proof of the rendered result —
see .claude/tdd-handoff.md "Behavioral Test Plan".

Explicitly OUT of scope per team-lead ruling (2026-07-13): the rendered cards
must NOT include a sparkline — the run endpoint does not return equity points
and fabricating them client-side is forbidden. No test in this file asserts
sparkline markup.

Assert structure/contract, never hardcoded computed values.
"""

from __future__ import annotations

import pathlib
import re

_JS_PATH = pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js"


def _js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _sb_run_analysis_body(src: str) -> str:
    """Extract the sbRunAnalysis function body (signature through its REAL
    closing brace, found via brace-matching from the function's own opening
    '{' — not a fixed-size character window. A prior fixed 4000-char window
    ("comfortably covers the ~70-line pre-fix version") silently truncated
    mid-function once fa691f6a's Checkpoint-3 field-consumption wiring grew
    the function to ~8000 chars, producing a spurious "Unbalanced braces"
    failure in _success_branch below — a test-harness assumption invalidated
    by legitimate growth, not a real code defect (confirmed: node --check
    clean, JS well-formed). Brace-matching is correct-by-construction
    regardless of how large the function grows in the future.
    """
    start = src.find("function sbRunAnalysis(")
    assert start != -1, "function sbRunAnalysis not found in static/ai_advisor.js"
    open_brace_idx = src.find("{", start)
    assert open_brace_idx != -1, "no opening brace found after sbRunAnalysis signature"
    close_idx = _matching_brace_end(src, open_brace_idx)
    return src[start : close_idx + 1]


def _post_response_segment(src: str) -> str:
    """Extract the segment AFTER the POST response is parsed — i.e. everything
    that runs once `data` is available. This isolates the success-handling logic
    from the request-building logic above it, so assertions about "no re-fetch
    after the response resolves" don't false-positive on the CSRF-token fetch or
    the initial POST itself.
    """
    body = _sb_run_analysis_body(src)
    marker = "var data = await resp.json();"
    idx = body.find(marker)
    assert idx != -1, (
        "Could not find 'var data = await resp.json();' in sbRunAnalysis — "
        "the response-parsing statement must stay present for this contract to "
        "be checkable at all."
    )
    return body[idx + len(marker) :]


def _matching_brace_end(text: str, open_brace_idx: int) -> int:
    """Return the index of the '}' that closes the '{' at open_brace_idx,
    accounting for nesting.
    """
    assert text[open_brace_idx] == "{", "open_brace_idx must point at a '{'"
    depth = 0
    i = open_brace_idx
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError("Unbalanced braces — no matching '}' found for sbRunAnalysis")


def _success_branch(src: str) -> str:
    """Extract ONLY the else-branch of `if (data.error) { ... } else { ... }`
    — i.e. the code that runs when the POST resolves WITHOUT an error.

    Scoped via brace-matching (not a fixed-size window) specifically because
    the current code's error/catch branches both happen to touch
    `resultsDiv.innerHTML` too (clearing it) — a window-based check for
    "resultsDiv.innerHTML is assigned" would false-positive on THOSE branches
    and never actually prove the SUCCESS branch renders anything.
    """
    segment = _post_response_segment(src)
    m = re.search(r"\}\s*else\s*\{", segment)
    assert m is not None, (
        "Could not find the '} else { ... }' success branch in sbRunAnalysis's "
        "post-response handling. The fix changes what runs on success, not "
        "the if(data.error)/else branching structure itself — a 0-survivor "
        "run is still `!data.error`, so it must still flow through this "
        "branch (see the plan: '0-survivor run shows an explicit honest "
        "status', not a separate branch)."
    )
    open_brace_idx = segment.index("{", m.end() - 1)
    close_idx = _matching_brace_end(segment, open_brace_idx)
    return segment[open_brace_idx : close_idx + 1]


# ---------------------------------------------------------------------------
# AC-1 — no unconditional navigation away from the tab on success
# ---------------------------------------------------------------------------


class TestNoUnconditionalNavigationOnSuccess:
    """AC-1: a successful run must render in-place, never navigate away."""

    def test_no_unconditional_navigation_after_response_resolves(self):
        """FAILS on current: the else-branch does `window.location.href = '/ai-advisor'`
        unconditionally on any non-error response — discarding the JSON.

        AC-1: the post-response segment must contain zero occurrences of a
        window.location.href reassignment to the SPA route. Navigating away is
        exactly the defect this AC removes.
        """
        segment = _post_response_segment(_js())
        assert "window.location.href" not in segment, (
            "AC-1 defect: sbRunAnalysis still navigates away via "
            "window.location.href after the POST resolves. The response JSON "
            "must be rendered in-place instead of discarded by a full-page "
            "navigation. Found in the post-response segment:\n" + segment
        )
        assert "location.reload" not in segment, (
            "AC-1: a location.reload() after the response resolves has the same "
            "discard-the-response effect as a href reassignment — also forbidden."
        )

    def test_success_branch_no_longer_purely_navigational(self):
        """Regression guard: the function must still exist and still be reachable
        (i.e. the fix doesn't just delete sbRunAnalysis or break its call sites).
        """
        src = _js()
        assert "function sbRunAnalysis(" in src, (
            "sbRunAnalysis must still be defined — the fix changes its body, not its existence."
        )
        assert "window.sbRunAnalysis = sbRunAnalysis;" in src, (
            "sbRunAnalysis must still be exposed on window for the Jinja "
            "onclick handler to call it."
        )


# ---------------------------------------------------------------------------
# AC-1 — the response is rendered using the route's own JSON fields
# ---------------------------------------------------------------------------


class TestRendersFromResponseFields:
    """AC-1: the success path must render survivors/rejected/n_candidates/
    fdr_adjusted_threshold from the POST response — the exact fields the route
    returns (app.py:4711-4719, confirmed unchanged by this cycle).
    """

    def test_success_path_references_survivors_field(self):
        segment = _post_response_segment(_js())
        assert "data.survivors" in segment, (
            "AC-1: the success path must read data.survivors from the route's "
            "response to render survivor cards. Not found in the post-response "
            "segment — the response is being discarded, not rendered."
        )

    def test_success_path_references_rejected_field(self):
        segment = _post_response_segment(_js())
        assert "data.rejected" in segment, (
            "AC-1: the success path must read data.rejected from the route's "
            "response to render the rejected-candidates section (count + "
            "expandable, per the plan). Not found in the post-response segment."
        )

    def test_success_path_references_n_candidates_field(self):
        segment = _post_response_segment(_js())
        assert "data.n_candidates" in segment, (
            "AC-1: the success path must read data.n_candidates so a 0-survivor "
            "run can show 'Evaluated N candidates — 0 passed the gate' instead "
            "of a blank/redirect. Not found in the post-response segment."
        )

    def test_success_path_writes_into_results_div(self):
        """Scoped to the else-branch specifically (via _success_branch) — NOT
        the whole post-response segment. The error AND catch branches both
        already do `resultsDiv.innerHTML = '';` on current code (clearing it),
        so a whole-segment check would false-positive without proving the
        SUCCESS branch renders anything at all.
        """
        branch = _success_branch(_js())
        assert "resultsDiv" in branch, (
            "AC-1: the success path must write into the sb-run-results "
            "container (resultsDiv) — the same element the loading state uses. "
            "If the response is rendered anywhere else the tab does not show "
            "the outcome without navigating away."
        )
        assert "resultsDiv.innerHTML" in branch, (
            "AC-1: resultsDiv must actually be populated (assigned innerHTML) "
            "INSIDE THE SUCCESS BRANCH, not merely referenced/cleared "
            "elsewhere (e.g. the error or catch branches, which already do "
            "this on the unfixed code)."
        )

    def test_no_sparkline_field_referenced(self):
        """Regression guard per team-lead ruling (2026-07-13): the run endpoint
        does not return equity points; the render must NOT fabricate a
        sparkline client-side. This is a scope boundary, not an oversight.
        """
        segment = _post_response_segment(_js())
        assert "sparkline" not in segment.lower(), (
            "AC-1 scope violation: the on-demand run response has no equity "
            "points — do not render or fabricate a sparkline for these cards."
        )


# ---------------------------------------------------------------------------
# AC-2 — run-scoping: rendered from THIS response, not a re-fetched history slice
# ---------------------------------------------------------------------------


class TestRunScopedNoRefetch:
    """AC-2: the operator must be able to tell THIS run's results from prior
    history. The chosen mechanism (per the plan's second option) is rendering
    the route's own response directly — proven here by asserting no additional
    network fetch happens after the POST resolves. A card set built entirely
    from `data` (already asserted above) is inherently scoped to this run;
    any subsequent fetch would reintroduce the risk of re-rendering a stale
    limit-50 history slice instead.
    """

    def test_no_additional_fetch_after_response_resolves(self):
        """FAILS if the fix re-fetches history instead of rendering `data` directly.

        The only network call in this function must be the original POST
        (already issued above the `var data = await resp.json();` marker) —
        nothing after the response resolves may call fetch(...) again.
        """
        segment = _post_response_segment(_js())
        assert "fetch(" not in segment, (
            "AC-2 defect: a fetch(...) call was found after the POST response "
            "resolved. Rendering must come from `data` (this run's own "
            "response) directly — re-fetching risks showing a stale limit-50 "
            "history slice instead of this run's outcome. Post-response "
            "segment:\n" + segment
        )
