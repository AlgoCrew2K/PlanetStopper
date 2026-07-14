"""RED tests — R2-1 AC-5: sbRunAnalysis() renders run-level generation provenance.

Contract pinned (locked with r2-fe + r2-engine via SendMessage before this
file was written):

  static/ai_advisor.js's sbRunAnalysis() gains a new NON-NULL-ONLY render
  block reading data.provenance (the route's 4-key JSON object:
  generation_model / mode / evidence_injected / run_id), stamped with
  data-testid="sb-live-generation-provenance" — deliberately DISTINCT from
  the pre-existing data-testid="sb-live-provenance" (the AC-11/F5 built-new-
  vs-Atlas rollup, app.py:4901-4908 / static/ai_advisor.js:778-781), which is
  a different concept sharing the same overloaded English word.

WHY SOURCE-CONSUMPTION GUARDS, NOT DOM TESTS: this stack has no JS-behavior
test runner (no jsdom/Jest/Playwright-component harness — confirmed via
CLAUDE.md's JS-testing gotcha: only `node --check` syntax validation exists
project-wide). These tests read static/ai_advisor.js as TEXT and assert
field names / testids are referenced as literal tokens inside sbRunAnalysis()'s
source — necessary but not sufficient (proves the field/testid is wired in,
not that the resulting DOM is visible/styled). Mirrors the exact idiom
established in tests/ai_advisor/test_r1_sb_live_run_field_consumption.py.
"""

from __future__ import annotations

import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_AI_ADVISOR_JS = _PROJECT_ROOT / "static" / "ai_advisor.js"

_FN_START_ANCHOR = "async function sbRunAnalysis()"
_FN_END_ANCHOR = "// Expose on window"

# The pre-existing testid this feature must NOT collide with (AC-11/F5 rollup).
_EXISTING_PROVENANCE_TESTID = "sb-live-provenance"
_NEW_TESTID = "sb-live-generation-provenance"


def _js() -> str:
    return _AI_ADVISOR_JS.read_text(encoding="utf-8")


def _sb_run_analysis_block(source: str) -> str:
    """The full sbRunAnalysis() function body — same text-window extraction
    established in test_r1_sb_live_run_field_consumption.py."""
    start = source.find(_FN_START_ANCHOR)
    assert start != -1, f"static/ai_advisor.js no longer contains {_FN_START_ANCHOR!r}"
    end = source.find(_FN_END_ANCHOR, start)
    assert end != -1, (
        f"static/ai_advisor.js no longer contains {_FN_END_ANCHOR!r} after sbRunAnalysis()"
    )
    return source[start:end]


# ---------------------------------------------------------------------------
# Self-guard: prove the extraction itself works before trusting any assertion
# below (same pattern as the R1 file — a broken anchor would vacuously pass
# every negative-style check).
# ---------------------------------------------------------------------------


def test_self_guard_sb_run_analysis_block_extraction_finds_known_existing_token():
    """candidate_id is already consumed by the current card() helper — if this
    fails, the anchors are stale and every other test in this file is testing
    nothing."""
    block = _sb_run_analysis_block(_js())
    assert "candidate_id" in block, (
        "self-guard failed: sbRunAnalysis() no longer references candidate_id — "
        "the block-extraction anchors are stale, fix them before trusting any "
        "other assertion in this file"
    )


def test_new_testid_not_yet_present_before_implementation():
    """Precondition guard (passes today, flips once GREEN lands): the NEW
    testid literal must be ABSENT from the file before implementation — proves
    this cycle is genuinely adding it, not re-discovering an existing string."""
    source = _js()
    assert f'data-testid="{_NEW_TESTID}"' not in source, (
        f'data-testid="{_NEW_TESTID}" is already present before R2-1 implementation — '
        "either this name collides with pre-existing markup, or GREEN already landed "
        "out of band. Either way this precondition check needs review."
    )


# ===========================================================================
# AC-5: sbRunAnalysis() consumes provenance / generation_model / run_id.
# ===========================================================================


def test_sb_run_analysis_consumes_provenance_field():
    """MUST FAIL pre-fix: sbRunAnalysis() never references data.provenance —
    the route JSON's new provenance object never reaches the live-run DOM.

    ADVERSARIAL note: asserts the 'data.provenance' TOKEN specifically, not a
    bare 'provenance' substring — the existing data-testid="sb-live-provenance"
    (AC-11/F5 rollup) already contains the substring 'provenance' today, which
    would make a bare substring check vacuously pass for the wrong reason."""
    block = _sb_run_analysis_block(_js())
    assert "data.provenance" in block, (
        "AC-5 GAP: sbRunAnalysis() does not consume data.provenance — the run-level "
        "generation provenance the route now computes is invisible on a live run."
    )


def test_sb_run_analysis_consumes_generation_model_field():
    """MUST FAIL pre-fix: generation_model is never read."""
    block = _sb_run_analysis_block(_js())
    assert "generation_model" in block, (
        "AC-5 GAP: sbRunAnalysis() does not consume provenance.generation_model."
    )


def test_sb_run_analysis_consumes_run_id_field():
    """MUST FAIL pre-fix: run_id is never read — the operator has no way to
    trace a live run's proposals back to their provenance/persisted rows."""
    block = _sb_run_analysis_block(_js())
    assert "run_id" in block, "AC-5/AC-6 GAP: sbRunAnalysis() does not consume provenance.run_id."


def test_sb_run_analysis_contains_new_generation_provenance_testid():
    """MUST FAIL pre-fix: the new, disambiguated data-testid literal is absent."""
    block = _sb_run_analysis_block(_js())
    assert f'data-testid="{_NEW_TESTID}"' in block, (
        f'AC-5 GAP: sbRunAnalysis() does not render data-testid="{_NEW_TESTID}" — '
        "the provenance block is not present or uses a different/undocumented testid."
    )


def test_sb_run_analysis_new_testid_distinct_from_existing_rollup_testid():
    """ADVERSARIAL: once the new block exists, it must use the NEW testid, not
    accidentally reuse the existing 'sb-live-provenance' (AC-11/F5 built-new/
    Atlas rollup) testid — collision would make both concepts unselectable."""
    block = _sb_run_analysis_block(_js())
    if f'data-testid="{_NEW_TESTID}"' not in block:
        # The RED test above already catches "not implemented yet" — nothing
        # further to check until it exists.
        return
    # The existing rollup's testid must still be present too (untouched feature).
    assert f'data-testid="{_EXISTING_PROVENANCE_TESTID}"' in block, (
        "Regression: the pre-existing AC-11/F5 built-new/Atlas rollup testid "
        f"'{_EXISTING_PROVENANCE_TESTID}' must remain present and untouched."
    )


# ---------------------------------------------------------------------------
# Non-null-only rendering (mirrors the mode_notice/backtest_unavailable idiom
# already established in this exact function).
# ---------------------------------------------------------------------------


def test_provenance_block_is_guarded_non_null_only():
    """MUST FAIL pre-fix: once wired, provenance access must be guarded by a
    truthiness/non-null check (`data.provenance` / `!= null`) BEFORE the field
    is dereferenced — mirrors the existing `if (data.built_new_count != null
    ...)` / `if (data.mode_notice)` idiom in this same function, so a run
    with provenance=None (the no-key error path) never throws a client-side
    TypeError on `data.provenance.generation_model`."""
    block = _sb_run_analysis_block(_js())
    if "provenance" not in block:
        # RED test above already catches the not-implemented-yet case.
        return
    guarded = (
        "data.provenance &&" in block
        or "data.provenance !=" in block
        or "data.provenance !== null" in block
        or "if (data.provenance)" in block
    )
    assert guarded, (
        "AC-3/AC-5 GAP: sbRunAnalysis() references data.provenance without an evident "
        "truthiness/non-null guard before it — a null provenance (no-key error path) "
        "would throw a client-side TypeError instead of degrading honestly. Expected one "
        "of: 'data.provenance &&', 'data.provenance !=', 'data.provenance !== null', "
        "'if (data.provenance)'."
    )
