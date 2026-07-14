"""RED tests — R2-3 AC-9: static/ai_advisor_asset_swaps.js's renderResults()
renders run-level generation provenance.

Contract pinned (mirrors R2-2's test_lc_live_generation_provenance_render.py;
coordinated with r2-3-fe via SendMessage before this file was written and
REVISED after r2-3-fe's follow-up correction, both incorporated below):

  static/ai_advisor_asset_swaps.js's renderResults(data) gains a new render
  block reading data.provenance (the route's 4-key JSON object: generation_model
  / mode / evidence_injected / run_id), stamped with
  data-testid="as-live-generation-provenance" — distinct from SB's
  "sb-live-generation-provenance" and Logic Changes' "lc-live-generation-provenance".

  SCOPE (REVISED, r2-3-fe's correction — two genuinely distinct error
  categories, not one):
    1. Genuine TRANSPORT failure (thrown Error / non-ok HTTP status before
       JSON parse, handled in evaluateSwap()'s .catch()) — no JSON body ever
       reaches the client, so there is no provenance to show. renderError()
       stays UNTOUCHED for this category — nothing to render, no test here
       requires a change to it.
    2. In-band `body.error` JSON response (200 status, valid JSON — no-key,
       hash-resolution-failure, tree-fetch-failure, the exactly-one-ticker
       error, the engine-exception type(exc).__name__ branch) — the route
       ALWAYS populates a real `provenance` on these paths (AC-8's whole
       mandate). This must be rendered via renderResults(data), the SAME
       function that renders survivors/no-survivors — mirroring
       static/ai_advisor_logic_changes.js's _renderResults(), which computes
       provenanceHtml BEFORE the data.error branch split and returns
       provenanceHtml + the error block on that branch (testid "error-state",
       reused here for consistency across AI Advisor surfaces). evaluateSwap()
       must therefore call renderResults(body) UNCONDITIONALLY on a parsed
       JSON response — no early dispatch to renderError() on body.error.

WHY SOURCE-CONSUMPTION GUARDS, NOT DOM TESTS: this stack has no JS-behavior
test runner (confirmed via CLAUDE.md's JS-testing gotcha: only `node --check`
syntax validation exists project-wide). These tests read
static/ai_advisor_asset_swaps.js as TEXT and assert field names / testids are
referenced as literal tokens — necessary but not sufficient (proves the
field/testid is wired in, not that the resulting DOM is visible/styled).
Mirrors the exact idiom established in test_lc_live_generation_provenance_render.py.
"""

from __future__ import annotations

import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_AS_JS = _PROJECT_ROOT / "static" / "ai_advisor_asset_swaps.js"

_FN_START_ANCHOR = "function renderResults("
_FN_END_ANCHOR = "function renderError("
_NEW_TESTID = "as-live-generation-provenance"
_ERROR_TESTID = "error-state"  # reused verbatim from static/ai_advisor_logic_changes.js


def _js() -> str:
    return _AS_JS.read_text(encoding="utf-8")


def _render_results_block(source: str) -> str:
    """The renderResults(...) function body — text-window extraction from its
    opening anchor to the next top-level function (renderError), mirroring
    the established text-window idiom in test_lc_live_generation_provenance_render.py.
    renderError() must still exist as a separate function AFTER renderResults
    in the source (it stays, untouched, for the transport-failure category)."""
    start = source.find(_FN_START_ANCHOR)
    assert start != -1, f"static/ai_advisor_asset_swaps.js no longer contains {_FN_START_ANCHOR!r}"
    end = source.find(_FN_END_ANCHOR, start)
    assert end != -1, "static/ai_advisor_asset_swaps.js structure changed — end anchor not found."
    return source[start:end]


def _evaluate_swap_block(source: str) -> str:
    """The evaluateSwap(...) function body — from its own anchor to end of
    file (it's the last top-level function in this file today)."""
    start = source.find("function evaluateSwap(")
    assert start != -1, "static/ai_advisor_asset_swaps.js no longer contains evaluateSwap()."
    return source[start:]


# ---------------------------------------------------------------------------
# Self-guard
# ---------------------------------------------------------------------------


def test_self_guard_render_results_block_extraction_finds_known_existing_token():
    """resultsArea.innerHTML is already assigned inside the current
    renderResults — if this fails, the anchors are stale and every other test
    here tests nothing."""
    block = _render_results_block(_js())
    assert "resultsArea.innerHTML" in block, (
        "self-guard failed: renderResults() no longer assigns resultsArea.innerHTML — "
        "the block-extraction anchors are stale, fix them before trusting any other "
        "assertion in this file."
    )


# ===========================================================================
# AC-9: renderResults() consumes provenance / generation_model / run_id.
# ===========================================================================


def test_render_results_consumes_provenance_field():
    block = _render_results_block(_js())
    assert "provenance" in block, (
        "AC-9 GAP: renderResults() does not reference provenance — the run-level "
        "generation provenance the route now computes is invisible on a live run."
    )


def test_render_results_consumes_generation_model_field():
    block = _render_results_block(_js())
    assert "generation_model" in block, (
        "AC-9 GAP: renderResults() does not consume provenance.generation_model."
    )


def test_render_results_consumes_run_id_field():
    block = _render_results_block(_js())
    assert "run_id" in block, "AC-9 GAP: renderResults() does not consume provenance.run_id."


def test_render_results_contains_new_as_generation_provenance_testid():
    block = _render_results_block(_js())
    assert f'data-testid="{_NEW_TESTID}"' in block, (
        f'AC-9 GAP: renderResults() does not render data-testid="{_NEW_TESTID}".'
    )


def test_provenance_block_is_guarded_non_null_only():
    """Defensive: provenance access must be guarded by a truthiness/non-null
    check before dereferencing fields — protects against a stale-cached JS
    talking to an old/legacy JSON shape."""
    block = _render_results_block(_js())
    guarded = (
        "provenance &&" in block
        or "provenance !=" in block
        or "provenance !== null" in block
        or "(provenance)" in block
    )
    assert guarded, (
        "AC-9 GAP: renderResults() references provenance without an evident "
        "truthiness/non-null guard before it."
    )


# ===========================================================================
# REVISED SCOPE: in-band body.error handled INSIDE renderResults(), with
# provenance still rendered alongside it — mirrors logic-changes.js's
# _renderResults() data.error branch exactly.
# ===========================================================================


def test_render_results_handles_data_error_internally():
    """renderResults() must branch on data.error itself (mirrors
    static/ai_advisor_logic_changes.js's _renderResults()) — the in-band
    JSON error category is NOT dispatched to a separate function."""
    block = _render_results_block(_js())
    assert "data.error" in block or "result.error" in block, (
        "AC-9 GAP: renderResults() does not check for an in-band error field on its "
        "own input — the route's populated provenance on error responses (no-key, "
        "hash-resolution-failure, exactly-one-ticker, engine-exception) would never "
        "be rendered if body.error is dispatched elsewhere before reaching this function."
    )


def test_render_results_error_branch_carries_error_testid():
    block = _render_results_block(_js())
    assert f'data-testid="{_ERROR_TESTID}"' in block, (
        f'AC-9 GAP: renderResults() does not render data-testid="{_ERROR_TESTID}" for the '
        "in-band error case (reused from logic-changes.js for cross-surface consistency)."
    )


def test_provenance_computed_before_error_branch_split():
    """ADVERSARIAL (mirrors logic-changes.js's own precedent): the provenance
    render logic must be computed/positioned so it is NOT exclusively inside
    the post-error-branch success path — otherwise the operator never sees it
    on an error response despite the route populating a real one. Proven by
    requiring the 'provenance' token to appear in the source at or before the
    first in-band-error check."""
    block = _render_results_block(_js())
    error_check_idx = block.find("data.error")
    if error_check_idx == -1:
        error_check_idx = block.find("result.error")
    provenance_idx = block.find("provenance")
    assert error_check_idx != -1, (
        "self-guard: no in-band error check found in renderResults() — see the "
        "dedicated test above for the primary failure signal."
    )
    assert provenance_idx != -1, "AC-9 GAP: 'provenance' not found at all in renderResults()."
    assert provenance_idx <= error_check_idx, (
        "AC-9 GAP: the provenance render logic appears to be reachable only AFTER the "
        "in-band error branch — since the route populates provenance on error responses "
        "too, the operator would never see it on a failed run. Provenance handling must "
        "be positioned before (or shared across) the error branch split."
    )


def test_evaluate_swap_calls_render_results_unconditionally_on_parsed_json():
    """ADVERSARIAL: evaluateSwap()'s fetch .then() handler must NOT early-
    dispatch body.error to renderError() before renderResults() ever sees it
    — that would make the revised in-band-error handling inside
    renderResults() unreachable from the real request path. The old
    'if (body.error) { renderError(body.error); return; }' pattern must be
    gone; renderResults(body) must be called regardless of body.error."""
    block = _evaluate_swap_block(_js())
    assert "renderError(body.error)" not in block, (
        "AC-9 GAP: evaluateSwap() still dispatches body.error to renderError() before "
        "renderResults() runs — the route's populated error-path provenance would never "
        "reach the DOM. Call renderResults(body) unconditionally on a parsed JSON response."
    )
    assert "renderResults(body)" in block, (
        "AC-9 GAP: evaluateSwap() no longer calls renderResults(body) on the parsed "
        "JSON response — the results/error rendering path is unreachable."
    )


# ---------------------------------------------------------------------------
# Existing DOM testids preserved (team-lead ruling: unified array-driven
# renderer approved PROVIDED it keeps the existing testids).
# ---------------------------------------------------------------------------


def test_existing_swap_card_testids_preserved():
    source = _js()
    for testid in ("swap-card-survivor", "swap-card-rejected", "swap-card-error"):
        assert testid in source, (
            f"AC-9 GAP (regression): existing testid {testid!r} no longer appears in "
            "static/ai_advisor_asset_swaps.js — the R2-3 render rewrite must preserve it."
        )
