"""RED tests — R2-3 AC-9: static/ai_advisor_asset_swaps.js's renderResults()
renders run-level generation provenance.

Contract pinned (mirrors R2-2's test_lc_live_generation_provenance_render.py;
coordinated with r2-3-fe via SendMessage before this file was written):

  static/ai_advisor_asset_swaps.js's renderResults(data) gains a new render
  block reading data.provenance (the route's 4-key JSON object: generation_model
  / mode / evidence_injected / run_id), stamped with
  data-testid="as-live-generation-provenance" — distinct from SB's
  "sb-live-generation-provenance" and Logic Changes' "lc-live-generation-provenance".

  Scope call (test-writer's, flagged to team-lead/r2-3-fe): unlike Logic
  Changes' unified _renderResults() (handles both error+success internally),
  this file dispatches to a SEPARATE renderError() on body.error. Provenance
  rendering is scoped to renderResults()'s two branches (survivor and
  no-survivor / objective-only reasoned mode) only — NOT extended into the
  hard-network-error renderError() path, per AC-9's literal "survivors AND
  zero-survivor paths" wording.

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


def _js() -> str:
    return _AS_JS.read_text(encoding="utf-8")


def _render_results_block(source: str) -> str:
    """The renderResults(...) function body — text-window extraction from its
    opening anchor to the next top-level function (renderError), mirroring
    the established text-window idiom in test_lc_live_generation_provenance_render.py."""
    start = source.find(_FN_START_ANCHOR)
    assert start != -1, f"static/ai_advisor_asset_swaps.js no longer contains {_FN_START_ANCHOR!r}"
    end = source.find(_FN_END_ANCHOR, start)
    assert end != -1, "static/ai_advisor_asset_swaps.js structure changed — end anchor not found."
    return source[start:end]


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
