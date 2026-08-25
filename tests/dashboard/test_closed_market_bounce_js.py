"""
RED test -- closed-market cumulative bounce, CLIENT leg (DE-CLOSED-BOUNCE-001).

Companion to tests/app/test_closed_market_cumulative_bounce.py (the SERVER leg:
closed_frozen portfolio_strip must carry guard_alpha/window). This file pins
the client-side invariant per team-lead's Gate-2 HOW steer: updateComparisonRows
must refuse to source the 'cumulative' row's value from a windowed/fallback
payload, not merely have fetchWindowedStrip stop calling it.

Root cause (index.js, verified against pre-fix code):
  - updateComparisonRows (line 977) already has an established discriminator
    for exactly this producer-shape ambiguity: `'data_as_of' in ps` (BL-4/
    DE-AUDIT-BL4-001, line 992) -- unconditionally set by every real /api/state
    poll's _compute_portfolio_strip (app.py) and NEVER set by
    analytics.compute_windowed_portfolio_strip (the windowed-strip producer).
  - BL4-001 wired that discriminator to the account-basis CHIP helpers only
    (renderAccountBasisChip / renderAccountBasisFreshness, lines 992-996) --
    never to the 'cumulative' row's VALUES (line 1006: `values:
    ps.cumulative_return || {}`, unconditional).
  - fetchWindowedStrip (line 1472) wraps /api/strip/<window>'s response (never
    carrying data_as_of) and calls updateComparisonRows(wrapped) (line 1481-
    1482) -- reaching this same unconditional line and overwriting the
    lifetime-labeled row with a VW-basis windowed value (analytics.py:1998-
    2000 documents the windowed strip is deliberately VW/cash-excluded basis).

This is a DIFFERENT bug from F-014 (DE-DISPLAY-TRUTH-001, see
tests/dashboard/test_display_truth_cluster_js.py's TestF014CumulativeLifetime
RowSourcesLifetimeValue): F-014 removed an explicit `ps.windowed_cumulative_
return ||` PREFERENCE inside this same row entry (already fixed, gone from
current code, and its own test still passes). This bug is a DIFFERENT
unguarded CALLER PATH (renderGuardAlpha's fallback) reaching the same
function's still-unconditional `ps.cumulative_return` fallback. F-014's test
requires the literal `ps.cumulative_return` reference to stay inside the
'cumulative' row entry -- this test's fix must coexist with that constraint,
not relocate the reference to an external helper.

NECESSARY-NOT-SUFFICIENT (mirrors test_display_truth_cluster_js.py's own
documented limitation -- this repo has no JS execution harness): a
source-text pin proves a discriminator construct exists, not that the DOM
genuinely never bounces at runtime. The full behavioral proof is bounce-ux's
live Playwright DOM-sampling gate against a real browser session.
"""

from __future__ import annotations

import pathlib
import re

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_INDEX_JS = _STATIC_DIR / "index.js"

_DISCRIMINATOR_RE = re.compile(r"data_as_of|\bbasis\b")


def _read_index_js() -> str:
    assert _INDEX_JS.exists(), "static/index.js must exist"
    return _INDEX_JS.read_text(encoding="utf-8")


def _slice_function(content: str, signature: str) -> str:
    """Full function body starting at `signature`, via brace counting --
    growth-safe. Mirrors test_display_truth_cluster_js.py's helper of the
    same name (duplicated here rather than imported across test modules,
    matching this repo's established per-file-duplication convention for
    small test-only utilities -- see test_held_basis_route_convergence.py's
    _frozen_datetime_class docstring for the precedent/rationale)."""
    start = content.find(signature)
    assert start != -1, f"{signature!r} not found in static/index.js"
    brace_start = content.index("{", start)
    depth = 0
    i = brace_start
    while i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
        i += 1
    raise AssertionError(f"Could not find matching closing brace for {signature!r}")


def _brace_match_end(body: str, open_brace_idx: int) -> int:
    depth = 0
    i = open_brace_idx
    while i < len(body):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError("unbalanced braces while scanning for a guard block's close")


def _guarded_by_wrapping_if(body: str, target_idx: int) -> bool:
    """True if target_idx sits inside SOME `if (...data_as_of|basis...) { }`
    block's braces anywhere in the function body -- covers a "wrap the whole
    rows array/forEach" fix shape, as opposed to an inline ternary local to
    the row entry itself (checked separately, see the test body)."""
    for m in re.finditer(r"if\s*\([^)]*(?:data_as_of|\bbasis\b)[^)]*\)\s*\{", body):
        brace_open = body.index("{", m.start())
        brace_close = _brace_match_end(body, brace_open)
        if brace_open < target_idx < brace_close:
            return True
    return False


class TestCumulativeRowGuardedByRealStatePollDiscriminator:
    def test_cumulative_row_values_gated_by_a_real_state_poll_discriminator(self):
        """RED: the 'cumulative' row entry sources ps.cumulative_return with
        nothing conditioning it on whether ps came from a genuine /api/state
        poll or a windowed-strip fallback payload. Deliberately does NOT pin
        one exact code shape (inline ternary local to the row entry, or an
        outer if-block wrapping the whole rows array/forEach) -- team-lead's
        steer left the mechanism to the implementer; the invariant is that
        SOME discriminator now conditions this specific reference, where
        today there is none.

        Uses the SAME row_entry slicing technique as the existing F-014 test
        (test_display_truth_cluster_js.py's TestF014CumulativeLifetimeRow
        SourcesLifetimeValue) for the inline-ternary check, since that test's
        `"ps.cumulative_return" in row_entry` requirement already constrains
        any fix to keep the literal reference local to this entry -- the most
        F-014-compatible shape is an inline conditional here, not a helper
        function extracted elsewhere in the file. The wrap-the-whole-block
        shape is checked as a fallback via brace-scope containment.
        """
        content = _read_index_js()
        body = _slice_function(content, "function updateComparisonRows(")

        row_anchor = body.find("id: 'cumulative'")
        assert row_anchor != -1, (
            "the `id: 'cumulative'` row entry was not found in updateComparisonRows "
            "-- this test's location assumptions may be stale; update the markers."
        )
        row_close = body.find("},", row_anchor)
        row_entry = body[row_anchor : row_close if row_close != -1 else row_anchor + 400]

        assert "ps.cumulative_return" in row_entry, (
            f"the 'cumulative' row entry no longer sources ps.cumulative_return at "
            f"all -- row entry: {row_entry!r}. This would also fail the pre-existing "
            f"F-014 test (test_display_truth_cluster_js.py); fix that regression "
            f"first before addressing this test."
        )

        inline_guarded = bool(_DISCRIMINATOR_RE.search(row_entry))
        wrapped_guarded = _guarded_by_wrapping_if(body, row_anchor)

        assert inline_guarded or wrapped_guarded, (
            f"FAIL: the 'cumulative' row entry sources ps.cumulative_return with no "
            f"discriminator (data_as_of / basis) conditioning it, either inline "
            f"within the entry or via an enclosing if-block -- row entry: "
            f"{row_entry!r}. This is the exact unconditional-sourcing bug that lets "
            f"fetchWindowedStrip's payload (which never carries data_as_of) "
            f"overwrite the 'Cumulative · lifetime' row with a VW-basis windowed "
            f"value on every closed-market poll (DE-CLOSED-BOUNCE-001)."
        )
