"""RED tests — R2-2 AC-8: static/ai_advisor_logic_changes.js's _renderResults()
renders run-level generation provenance.

Contract pinned (coordinated with r2-2-fe via SendMessage before this file was
written):

  static/ai_advisor_logic_changes.js's _renderResults(data) gains a new render
  block reading data.provenance (the route's 4-key JSON object: generation_model
  / mode / evidence_injected / run_id), stamped with
  data-testid="lc-live-generation-provenance" — distinct from SB's
  data-testid="sb-live-generation-provenance" (same overloaded concept, a
  different producing route). UNLIKE SB (where the route's error branch omits
  provenance), the logic-changes route now populates provenance on EVERY
  response (AC-5 route ruling) — so the block must be reachable from BOTH the
  data.error early-return branch and the normal survivors/no-survivors branch,
  not nested exclusively inside one.

WHY SOURCE-CONSUMPTION GUARDS, NOT DOM TESTS: this stack has no JS-behavior
test runner (no jsdom/Jest/Playwright-component harness — confirmed via
CLAUDE.md's JS-testing gotcha: only `node --check` syntax validation exists
project-wide). These tests read static/ai_advisor_logic_changes.js as TEXT and
assert field names / testids are referenced as literal tokens — necessary but
not sufficient (proves the field/testid is wired in, not that the resulting DOM
is visible/styled). Mirrors the exact idiom established in
tests/ai_advisor/test_sb_live_run_provenance_render.py.
"""

from __future__ import annotations

import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_LC_JS = _PROJECT_ROOT / "static" / "ai_advisor_logic_changes.js"

_FN_START_ANCHOR = "function _renderResults(data)"
_NEW_TESTID = "lc-live-generation-provenance"


def _js() -> str:
    return _LC_JS.read_text(encoding="utf-8")


def _render_results_block(source: str) -> str:
    """The full _renderResults(data) function body — text-window extraction
    from the function's opening brace to its closing top-level '}' before the
    next top-level function/comment block, mirroring the established
    text-window idiom. Uses the next '// ---' section divider as the end
    anchor (this file's own existing convention, see the 'Evaluate button'
    section comment immediately following _renderResults)."""
    start = source.find(_FN_START_ANCHOR)
    assert start != -1, (
        f"static/ai_advisor_logic_changes.js no longer contains {_FN_START_ANCHOR!r}"
    )
    end = source.find(
        "// ---------------------------------------------------------------------------\n    // Evaluate button",
        start,
    )  # noqa: E501
    assert end != -1, "static/ai_advisor_logic_changes.js structure changed — end anchor not found."
    return source[start:end]


# ---------------------------------------------------------------------------
# Self-guard
# ---------------------------------------------------------------------------


def test_self_guard_render_results_block_extraction_finds_known_existing_token():
    """survivors_detail is already consumed by the current _renderResults — if
    this fails, the anchors are stale and every other test here tests nothing."""
    block = _render_results_block(_js())
    assert "survivors_detail" in block, (
        "self-guard failed: _renderResults() no longer references survivors_detail — "
        "the block-extraction anchors are stale, fix them before trusting any other "
        "assertion in this file."
    )


# ===========================================================================
# AC-8: _renderResults() consumes provenance / generation_model / run_id.
# ===========================================================================


def test_render_results_consumes_provenance_field():
    block = _render_results_block(_js())
    assert "data.provenance" in block, (
        "AC-8 GAP: _renderResults() does not consume data.provenance — the run-level "
        "generation provenance the route now computes is invisible on a live run."
    )


def test_render_results_consumes_generation_model_field():
    block = _render_results_block(_js())
    assert "generation_model" in block, (
        "AC-8 GAP: _renderResults() does not consume provenance.generation_model."
    )


def test_render_results_consumes_run_id_field():
    block = _render_results_block(_js())
    assert "run_id" in block, "AC-8 GAP: _renderResults() does not consume provenance.run_id."


def test_render_results_contains_new_lc_generation_provenance_testid():
    block = _render_results_block(_js())
    assert f'data-testid="{_NEW_TESTID}"' in block, (
        f'AC-8 GAP: _renderResults() does not render data-testid="{_NEW_TESTID}".'
    )


# ---------------------------------------------------------------------------
# Reachable from BOTH the error branch and the normal branch (route always
# populates provenance now — unlike SB).
# ---------------------------------------------------------------------------


def test_provenance_render_reachable_before_error_branch_early_return():
    """ADVERSARIAL: the current _renderResults() early-returns immediately when
    data.error is truthy (`if (data.error) { return ...; }`), before any other
    rendering runs. Since the route now ALWAYS populates provenance (including
    on error responses), the provenance-consuming code must be positioned
    (or otherwise reachable) so it is not exclusively inside the post-error-
    branch code path — proven here by requiring the 'data.provenance' token to
    appear in the source at or before the first `if (data.error)` occurrence,
    i.e. computed/considered prior to the early-return branch split."""
    block = _render_results_block(_js())
    error_branch_idx = block.find("if (data.error)")
    provenance_idx = block.find("data.provenance")
    assert error_branch_idx != -1, (
        "self-guard: 'if (data.error)' branch not found in _renderResults()."
    )
    assert provenance_idx != -1, (
        "AC-8 GAP: 'data.provenance' not found at all in _renderResults() — see the "
        "dedicated consumption test above for the primary failure signal."
    )
    assert provenance_idx <= error_branch_idx, (
        "AC-8 GAP: the provenance render logic appears to be reachable only AFTER the "
        "data.error early-return — since the route now populates provenance on error "
        "responses too, the operator would never see it on a failed run. Provenance "
        "handling must be positioned before (or shared across) the error branch split."
    )


def test_provenance_block_is_guarded_non_null_only():
    """Defensive: provenance access must be guarded by a truthiness/non-null
    check before dereferencing fields — protects against a stale-cached JS
    talking to an old/legacy JSON shape."""
    block = _render_results_block(_js())
    guarded = (
        "data.provenance &&" in block
        or "data.provenance !=" in block
        or "data.provenance !== null" in block
        or "if (data.provenance)" in block
    )
    assert guarded, (
        "AC-8 GAP: _renderResults() references data.provenance without an evident "
        "truthiness/non-null guard before it."
    )
