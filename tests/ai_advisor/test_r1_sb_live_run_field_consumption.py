"""RED tests — r1-review Checkpoint-3 BLOCK finding: SB live-run render path
never consumes any of the R1 route-JSON fields (AC-7/AC-9/AC-11).

WHAT THE REVIEWER FOUND (grep-verified, confirmed by direct read before
writing): `static/ai_advisor.js`'s `sbRunAnalysis()` — the LIVE-RUN handler
wired to the "Run analysis" button (POST /ai-advisor/strategy-builder/run,
result rendered in-place, never a page navigation) — was untouched all
cycle. Its nested `card(c, cls)` helper renders ONLY `c.candidate_id`; the
success branch's summary line renders only `n_candidates`/
`fdr_adjusted_threshold`; the error branch renders only `data.error`. Zero
occurrences anywhere in ai_advisor.js of: `built_new_count`, `atlas_count`,
`mode_notice` (AC-11 run-level rollup + degraded notice), `screens_skipped`
(AC-12), `error_category` (AC-11 sanitized cause), `low_power` (AC-9),
`rejection_reason` (AC-7). Every route-JSON RED test this cycle proved the
FIELD reaches the JSON response — none of them touch this render path, so
they were structurally blind to this gap (see the meta-finding note in
.claude/tdd-handoff.md).

WHY SOURCE-CONSUMPTION GUARDS, NOT DOM TESTS: this stack has no JS-behavior
test runner (no jsdom/Jest/Playwright-component harness — confirmed via
CLAUDE.md's own JS-testing gotcha: only `node --check` syntax validation
exists project-wide). A test that claims to prove "the browser renders X"
without executing the JS in a browser would be fabricated confidence — do
NOT fake a DOM test this stack cannot support. These tests instead read
`static/ai_advisor.js` as TEXT and assert each field name is referenced as
a literal token inside `sbRunAnalysis()`'s source — a NECESSARY but NOT
SUFFICIENT check: it proves the implementer wired the field's NAME into the
function that will read `data.<field>`, but proves nothing about whether
the resulting DOM element is visible, styled, or reachable to an operator.
The SUFFICIENT verification for this render path is the PM's first-hand
browser E2E (now explicitly including a live degraded-run render check per
team-lead's message) — these tests exist to catch a regression that
silently drops a field reference, not to certify the live UI.
"""

from __future__ import annotations

import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_AI_ADVISOR_JS = _PROJECT_ROOT / "static" / "ai_advisor.js"

_FN_START_ANCHOR = "async function sbRunAnalysis()"
_FN_END_ANCHOR = "// Expose on window"
_ERROR_BRANCH_START = "if (data.error)"
_ERROR_BRANCH_END = "} else {"


def _js() -> str:
    return _AI_ADVISOR_JS.read_text(encoding="utf-8")


def _sb_run_analysis_block(source: str) -> str:
    """The full sbRunAnalysis() function body (start anchor through the
    'Expose on window' comment that immediately follows it) — a text window,
    not a JS parse, per the established `_js_block` pattern in
    tests/app/test_dollar_saved_display_contract.py."""
    start = source.find(_FN_START_ANCHOR)
    assert start != -1, f"static/ai_advisor.js no longer contains {_FN_START_ANCHOR!r}"
    end = source.find(_FN_END_ANCHOR, start)
    assert end != -1, (
        f"static/ai_advisor.js no longer contains {_FN_END_ANCHOR!r} after sbRunAnalysis()"
    )
    return source[start:end]


def _sb_run_analysis_error_branch(source: str) -> str:
    """Just the `if (data.error) { ... }` branch — scopes the error_category
    check to where an error-cause field would actually be read, distinct
    from the success-path fields checked elsewhere in this file."""
    block = _sb_run_analysis_block(source)
    start = block.find(_ERROR_BRANCH_START)
    assert start != -1, f"sbRunAnalysis() no longer contains {_ERROR_BRANCH_START!r}"
    end = block.find(_ERROR_BRANCH_END, start)
    assert end != -1, f"sbRunAnalysis() error branch has no {_ERROR_BRANCH_END!r} terminator"
    return block[start:end]


# ---------------------------------------------------------------------------
# Self-guard: prove the extraction itself works (a broken anchor would make
# every test below vacuously fail — this test would ALSO fail in that case,
# distinguishing "extraction broke" from "field genuinely missing").
# ---------------------------------------------------------------------------


def test_self_guard_sb_run_analysis_block_extraction_finds_known_existing_token():
    """candidate_id is already consumed by the current card() helper — if
    this fails, the anchors above no longer match the real file and every
    other test in this module is testing nothing."""
    block = _sb_run_analysis_block(_js())
    assert "candidate_id" in block, (
        "self-guard failed: sbRunAnalysis() no longer references candidate_id — "
        "the block-extraction anchors are stale, fix them before trusting any "
        "other assertion in this file"
    )


# ---------------------------------------------------------------------------
# AC-11: run-level rollup + degraded notice.
# ---------------------------------------------------------------------------


def test_ac11_sb_live_run_consumes_built_new_count_field():
    """MUST FAIL pre-fix: sbRunAnalysis() never references built_new_count —
    a live degraded run (0 built-new plans) is indistinguishable from a
    healthy one on this render path."""
    block = _sb_run_analysis_block(_js())
    assert "built_new_count" in block, (
        "AC-11 GAP: sbRunAnalysis() does not consume data.built_new_count — "
        "the live-run summary cannot render the built-new/Atlas provenance "
        "split the route already computes."
    )


def test_ac11_sb_live_run_consumes_atlas_count_field():
    """MUST FAIL pre-fix — sibling to built_new_count."""
    block = _sb_run_analysis_block(_js())
    assert "atlas_count" in block, "AC-11 GAP: sbRunAnalysis() does not consume data.atlas_count."


def test_ac11_sb_live_run_consumes_mode_notice_field():
    """MUST FAIL pre-fix: the AC-11 degraded notice the route computes
    (mode_notice) is never rendered on a live run — an operator running the
    live 'Run analysis' button cannot tell 'Opus produced nothing' from
    'Opus produced everything you see', the exact gap AC-11 exists to close,
    on the one surface where they'd actually see it happen in real time."""
    block = _sb_run_analysis_block(_js())
    assert "mode_notice" in block, (
        "AC-11 GAP: sbRunAnalysis() does not consume data.mode_notice — the "
        "degraded-run notice never reaches the live-run DOM."
    )


def test_ac11_sb_live_run_consumes_error_category_field():
    """MUST FAIL pre-fix: the error branch renders only data.error (the
    static sanitized token) — data.error_category (the richer, still-
    sanitized cause category AC-11 added) is never read."""
    error_block = _sb_run_analysis_error_branch(_js())
    assert "error_category" in error_block, (
        f"AC-11 GAP: sbRunAnalysis()'s error branch does not consume "
        f"data.error_category. Error branch source:\n{error_block}"
    )


# ---------------------------------------------------------------------------
# AC-9 / AC-7 / AC-12: per-candidate and screen-skip fields.
# ---------------------------------------------------------------------------


def test_ac9_sb_live_run_consumes_low_power_field():
    """MUST FAIL pre-fix: the card() helper renders only candidate_id —
    low_power (and therefore the statistical-power caveat) never reaches a
    live-run card."""
    block = _sb_run_analysis_block(_js())
    assert "low_power" in block, (
        "AC-9 GAP: sbRunAnalysis()/card() does not consume c.low_power — a "
        "live-run survivor card cannot carry the statistical-power caveat."
    )


def test_ac7_sb_live_run_consumes_rejection_reason_field():
    """MUST FAIL pre-fix: rejected cards on a live run render only
    candidate_id — rejection_reason (pbo_veto / below_spy_alpha /
    fdr_not_winner / oos_inferior_to_incumbent) is invisible, same class of
    honesty gap AC-7 closed on the persisted/route-JSON surfaces."""
    block = _sb_run_analysis_block(_js())
    assert "rejection_reason" in block, (
        "AC-7 GAP: sbRunAnalysis()/card() does not consume r.rejection_reason "
        "— a live-run rejected card cannot render a distinguishable cause."
    )


def test_ac12_sb_live_run_consumes_screens_skipped_field():
    """MUST FAIL pre-fix: the route's screens_skipped/screens_skipped_reason
    indicator (AC-12) is never rendered on a live run."""
    block = _sb_run_analysis_block(_js())
    assert "screens_skipped" in block, (
        "AC-12 GAP: sbRunAnalysis() does not consume data.screens_skipped."
    )
