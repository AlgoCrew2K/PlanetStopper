"""
RED tests -- F7 AC-2: the two BINDING render-surface defects (source-text
level -- this repo has no JS execution harness; the function-body-slice
idiom below mirrors tests/dashboard/test_cards_live_updates.py's
AC-CL.10/AC-CL.11 established rigor bar, confirmed acceptable at plan
approval).

Cycle context (feature-plans/math-f7.md + ADDENDUM 2, LOCKED at plan
approval)
---------------------------------------------------------------------------
1. MC-dial stale-skip (hard-fail case): ``renderMcDial`` (static/index.js
   ~831-860) returns early when ``mcProb === null`` (~840) instead of
   actively rendering an exited state; its two call sites --
   ``loadCharts`` (~329, unconditional) and ``updateCards`` (~1033-1037,
   ``!= null``-guarded) -- either never notice the null or actively skip
   the call. A triggered symphony's dial visually freezes at its last
   pre-trigger reading forever.

2. Chart-fallback resurrection (the cycle's sharpest binding case):
   ``renderRiskMathPanel`` (~656-745) backward-scans ``chartData.data`` for
   the last non-null ``mc_prob`` (~661-666), ignoring ``sym.triggered``
   (already an in-scope parameter). Once post-trigger rows carry
   ``mc_prob: null`` (per AC-1), this scan walks PAST them and resurrects
   the last PRE-trigger float as "current" for an exited symphony -- a
   null-scan cannot distinguish "no data yet" from "exited". The render
   must short-circuit on trigger STATE before ever reaching the scan.

``populateRiskMathFromState`` (~509-536, the eager bot_state-driven
populate) is a SEPARATE, ALREADY-correct code path (sentinelToNull-guarded)
-- it gets a regression-only proof here, not a fix expectation. Per
ADDENDUM: "already works is not evidence" -- proven via the poll-path route
test in tests/app/test_f7_ac2_poll_path_null_passthrough.py, not here.

RED STATUS (HEAD a904374d, no F7 code yet)
-------------------------------------------
* test_render_mc_dial_references_triggered_state -- RED (no "trigger"
  reference anywhere in the current function).
* test_load_charts_call_site_does_not_unconditionally_skip_exited_dial /
  test_update_cards_call_site_is_triggered_aware -- RED.
* test_render_risk_math_panel_checks_triggered_before_scanning -- RED (the
  backward scan today has no sym.triggered check at all, at any position).
* test_populate_risk_math_from_state_keeps_existing_sentinel_guard --
  GREEN already (regression pin on the untouched surface).
"""

from __future__ import annotations

import pathlib
import re

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_INDEX_JS = _STATIC_DIR / "index.js"


def _read_index_js() -> str:
    assert _INDEX_JS.exists(), "static/index.js must exist"
    return _INDEX_JS.read_text(encoding="utf-8")


def _slice_function(content: str, signature: str, max_len: int = 3000) -> str:
    """Return the text from the first occurrence of ``signature`` (a
    function declaration prefix like 'function foo(') up to the START of
    the NEXT top-level ``    function `` declaration (this file's 4-space
    indentation convention), capped at ``max_len``.

    Stopping at the next sibling function (rather than always taking a
    fixed char count, as test_cards_live_updates.py's AC-CL.11 does) avoids
    spilling into unrelated neighboring functions that may legitimately
    reference words this test greps for -- important here since
    "triggered" already appears elsewhere in this file (e.g. the
    status-pill block) for unrelated reasons.
    """
    start = content.find(signature)
    assert start != -1, f"{signature!r} not found in static/index.js"
    next_fn = content.find("\n    function ", start + len(signature))
    end = min(next_fn, start + max_len) if next_fn != -1 else start + max_len
    return content[start:end]


_TRIGGER_WORD_PATTERN = re.compile(r"\btrigger(ed)?\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# MC-dial stale-skip (hard-fail case)
# ---------------------------------------------------------------------------
def test_render_mc_dial_references_triggered_state() -> None:
    """renderMcDial's internal null-handling (~840, ``if (mcProb === null)
    return;``) must no longer be an UNCONDITIONAL skip -- an exited
    symphony needs an ACTIVE render (muted arc + "-" label), not a frozen
    last-good reading. Minimum signal: the function body now reasons about
    trigger/exited state at all, not silently returning on every null."""
    content = _read_index_js()
    body = _slice_function(content, "function renderMcDial(")
    assert _TRIGGER_WORD_PATTERN.search(body), (
        "AC-2 FAIL: renderMcDial has no reference to trigger/exited state "
        "anywhere in its body -- the current unconditional "
        "`if (mcProb === null) return;` (~index.js:840) freezes the dial at "
        "its last pre-trigger reading instead of actively rendering an "
        "honest exited state. The fix must reason about triggered/exited "
        "state, not just null-ness."
    )


def _window_around(content: str, needle: str, before: int = 200, after: int = 200) -> str:
    """A tight text window around ``needle``'s first occurrence -- used to
    check "is this SPECIFIC call site trigger-aware", not "does trigger
    appear anywhere in this ~2000-char function" (a 2000-char slice easily
    spans unrelated logic, e.g. the status-pill block, that already
    legitimately mentions sym.triggered today -- a broad match there would
    pass for the wrong reason)."""
    idx = content.find(needle)
    assert idx != -1, f"{needle!r} not found in static/index.js"
    return content[max(0, idx - before) : idx + len(needle) + after]


def test_load_charts_call_site_passes_or_derives_triggered_state_to_dial() -> None:
    """loadCharts (~324-332) calls renderMcDial unconditionally today,
    passing only the raw chart response -- it has no way to tell renderMcDial
    "this symphony is exited, render the exited state actively" unless the
    triggered/exited signal is threaded through (either via ``sym.triggered``
    already in scope at this call site, or an equivalent field on the
    passed chartData). Checked in a TIGHT window around the exact call
    site, not the whole function -- a broad slice risks a false pass from
    unrelated code."""
    content = _read_index_js()
    window = _window_around(content, "renderMcDial(sym.id, data)", before=300, after=50)
    assert _TRIGGER_WORD_PATTERN.search(window), (
        "AC-2 FAIL: loadCharts' renderMcDial call site has no trigger-state "
        "awareness within its immediate vicinity -- sym.triggered is already "
        "in scope at this call site (loadCharts iterates `sym` from "
        "botState) but is never threaded through, so renderMcDial cannot "
        f"distinguish exited from no-data-yet. Found window: {window!r}"
    )


def test_update_cards_call_site_is_triggered_aware() -> None:
    """updateCards' renderMcDial call site (~1033-1037) today reads:
    ``if (sym.mc_prob != null) { renderMcDial(id, {mc_prob: sym.mc_prob,
    data: []}); }`` -- a bare null-guard that SKIPS the call entirely for an
    exited symphony (mc_prob: null), leaving the dial frozen. Checked in a
    TIGHT window around the exact call site (not the whole ~2000-char
    updateCards body, which already legitimately mentions "triggered"
    elsewhere -- e.g. the status-pill block -- and would pass for the
    wrong reason on a broad slice)."""
    content = _read_index_js()
    update_cards_start = content.find("function updateCards(")
    assert update_cards_start != -1, "function updateCards not found in static/index.js."
    body = content[update_cards_start : update_cards_start + 2000]
    assert "renderMcDial" in body, "updateCards no longer calls renderMcDial at all."

    window = _window_around(body, "renderMcDial(id,", before=250, after=50)
    assert _TRIGGER_WORD_PATTERN.search(window), (
        "AC-2 FAIL: updateCards' MC-dial call site has no trigger-state "
        "awareness in its immediate vicinity -- the bare "
        "`sym.mc_prob != null` guard (~index.js:1036) skips the "
        "renderMcDial call entirely for an exited symphony instead of "
        f"actively rendering the exited state. Found window: {window!r}"
    )


# ---------------------------------------------------------------------------
# Chart-fallback resurrection (the sharpest binding case)
# ---------------------------------------------------------------------------
def test_render_risk_math_panel_checks_triggered_before_scanning() -> None:
    """renderRiskMathPanel's backward null-scan (~661-666) must consult
    ``sym.triggered`` (already an in-scope parameter -- no new plumbing
    needed) BEFORE reaching the scan loop -- a null-scan cannot distinguish
    "no data yet" from "exited"; only the STATE, not the data shape, can."""
    content = _read_index_js()
    body = _slice_function(content, "function renderRiskMathPanel(")

    trigger_match = _TRIGGER_WORD_PATTERN.search(body)
    assert trigger_match, (
        "AC-2 FAIL: renderRiskMathPanel never references sym.triggered "
        "anywhere -- the backward null-scan (~index.js:661-666) will "
        'resurrect the last PRE-trigger mc_prob as "current" for an '
        "exited symphony once post-trigger rows carry the honest None "
        "sentinel (AC-1). sym.triggered is already a parameter in scope; "
        "the render must short-circuit on it before the scan."
    )

    scan_loop_match = re.search(r"for\s*\(\s*var\s+i\s*=\s*data\.length\s*-\s*1", body)
    assert scan_loop_match, (
        "The backward-scan loop pattern was not found -- this test's "
        "location assumptions may be stale; update the slice/markers."
    )
    assert trigger_match.start() < scan_loop_match.start(), (
        "AC-2 FAIL: renderRiskMathPanel references sym.triggered SOMEWHERE, "
        "but not BEFORE the backward-scan loop -- the trigger-state check "
        "must short-circuit the render before the scan runs, not merely "
        "exist elsewhere in the function (e.g. only in an unrelated field "
        "like breakeven_locked's sym.triggered-adjacent styling)."
    )


# ---------------------------------------------------------------------------
# populateRiskMathFromState -- regression-only proof ("already correct")
# ---------------------------------------------------------------------------
def test_populate_risk_math_from_state_keeps_existing_sentinel_guard() -> None:
    """populateRiskMathFromState (~509-536) is a SEPARATE, already-correct
    eager-populate path (guards via sentinelToNull + typeof check) -- this
    is a regression pin, not a fix expectation. If a future change ever
    removes this guard, this test must catch it."""
    content = _read_index_js()
    body = _slice_function(content, "function populateRiskMathFromState(", max_len=1200)
    assert "sentinelToNull" in body, (
        "Regression: populateRiskMathFromState no longer calls "
        "sentinelToNull on sym.mc_prob -- this was the existing None-safe "
        "guard for the eager-populate path; do not remove it while fixing "
        "the OTHER (chart-driven) surface."
    )
    assert "sym.mc_prob" in body, (
        "Regression: populateRiskMathFromState no longer reads sym.mc_prob "
        "at all -- has the eager-populate wiring been removed?"
    )
