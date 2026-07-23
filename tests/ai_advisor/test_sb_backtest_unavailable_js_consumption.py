"""RED tests -- feature-plans/advisor-outage-degrade.md AC-4/AC-5 (JS render
layer): the live-run handler `sbRunAnalysis()` in static/ai_advisor.js must
consume `data.backtest_unavailable_notice` so an operator running the "Run
analysis" button sees the outage notice in real time, not just via the
route-JSON tests (which prove the field REACHES the response, not that the
DOM renders it).

WHY SOURCE-CONSUMPTION GUARDS, NOT DOM TESTS: this stack has no JS-behavior
test runner (no jsdom/Jest/Playwright-component harness -- confirmed via
CLAUDE.md's own JS-testing gotcha: only `node --check` syntax validation
exists project-wide). These tests read static/ai_advisor.js as TEXT and
assert the field name is referenced as a literal token inside
sbRunAnalysis()'s source -- a NECESSARY but NOT SUFFICIENT check (proves the
field's NAME is wired into the function that reads `data.<field>`, proves
nothing about DOM visibility/styling). Same discipline and pattern as
tests/ai_advisor/test_r1_sb_live_run_field_consumption.py (identical anchors
reused so both files stay in lockstep if the function is ever restructured).
The SUFFICIENT verification is the PM's first-hand browser E2E, per the R1
precedent file's own note.
"""

from __future__ import annotations

import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_AI_ADVISOR_JS = _PROJECT_ROOT / "static" / "ai_advisor.js"

_FN_START_ANCHOR = "async function sbRunAnalysis()"
_FN_END_ANCHOR = "// Expose on window"


def _js() -> str:
    return _AI_ADVISOR_JS.read_text(encoding="utf-8")


def _sb_run_analysis_block(source: str) -> str:
    """The full sbRunAnalysis() function body -- a text window, not a JS
    parse, identical extraction to test_r1_sb_live_run_field_consumption.py."""
    start = source.find(_FN_START_ANCHOR)
    assert start != -1, f"static/ai_advisor.js no longer contains {_FN_START_ANCHOR!r}"
    end = source.find(_FN_END_ANCHOR, start)
    assert end != -1, (
        f"static/ai_advisor.js no longer contains {_FN_END_ANCHOR!r} after sbRunAnalysis()"
    )
    return source[start:end]


# ---------------------------------------------------------------------------
# Self-guard: prove the extraction itself works (mirrors the R1 sibling
# file's self-guard so a broken anchor is distinguishable from a genuinely
# missing field).
# ---------------------------------------------------------------------------


def test_self_guard_sb_run_analysis_block_extraction_finds_known_existing_token():
    """mode_notice is already consumed (R1, landed) -- if this fails, the
    anchors are stale and every other assertion in this file is meaningless."""
    block = _sb_run_analysis_block(_js())
    assert "mode_notice" in block, (
        "self-guard failed: sbRunAnalysis() no longer references mode_notice — "
        "the block-extraction anchors are stale, fix them before trusting any "
        "other assertion in this file"
    )


# ---------------------------------------------------------------------------
# AC-4/AC-5: backtest_unavailable_notice must be consumed on the live-run
# render path.
# ---------------------------------------------------------------------------


def test_ac4_sb_live_run_consumes_backtest_unavailable_notice_field():
    """MUST FAIL pre-fix: sbRunAnalysis() never references
    backtest_unavailable_notice -- a live degraded run (Composer outage) is
    indistinguishable from a healthy one on this render path, the exact
    silent-zero gap this whole cycle exists to close."""
    block = _sb_run_analysis_block(_js())
    assert "backtest_unavailable_notice" in block, (
        "AC-4 GAP: sbRunAnalysis() does not consume data.backtest_unavailable_notice "
        "-- the outage notice the route computes never reaches the live-run DOM."
    )


def test_ac4_sb_live_run_renders_a_distinct_testid_for_the_outage_notice():
    """The rendered element must carry its own data-testid (same convention
    as sb-live-mode-notice / sb-live-screens-skipped) so it's distinguishable
    from every other empty-state/notice block on this render path -- not
    reused generic markup that a screenshot audit couldn't attribute."""
    block = _sb_run_analysis_block(_js())
    assert "sb-live-backtest-unavailable" in block, (
        "AC-4 GAP: no dedicated data-testid for the backtest-unavailable notice "
        "in sbRunAnalysis() -- it must be distinguishable from mode_notice/"
        "screens_skipped, not collapsed into an existing generic block."
    )


def test_ac4_backtest_unavailable_notice_render_is_non_null_only_guarded():
    """The notice must be rendered inside an `if (data.backtest_unavailable...)`
    guard (mirrors the existing mode_notice/screens_skipped non-null-only
    pattern) -- never unconditionally interpolated, which would render the
    literal string 'null'/'undefined' on a healthy run (AC-5 honest
    empty-state)."""
    block = _sb_run_analysis_block(_js())
    notice_idx = block.find("backtest_unavailable_notice")
    assert notice_idx != -1, "backtest_unavailable_notice not found (see sibling test)"
    # The nearest preceding `if (` on the same guard must reference either the
    # notice field itself or the sibling backtest_unavailable flag -- proving
    # the render is conditional, not unconditional string interpolation.
    preceding = block[:notice_idx]
    last_if = preceding.rfind("if (data.backtest_unavailable")
    assert last_if != -1, (
        "backtest_unavailable_notice must be rendered inside an "
        "`if (data.backtest_unavailable...)` guard, not interpolated unconditionally"
    )
