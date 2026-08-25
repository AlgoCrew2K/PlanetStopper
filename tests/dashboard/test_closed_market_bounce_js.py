"""
RED test -- closed-market cumulative bounce, CLIENT leg (DE-CLOSED-BOUNCE-001).

Companion to tests/app/test_closed_market_cumulative_bounce.py (the SERVER leg:
closed_frozen portfolio_strip must carry guard_alpha/window). This file pins
the client-side invariant per team-lead's Gate-2 HOW steer: updateComparisonRows
must refuse to source a comparison row's value from a windowed/fallback
payload, not merely have fetchWindowedStrip stop calling it.

Root cause (index.js, verified against pre-fix code):
  - updateComparisonRows (line 977) already has an established discriminator
    for exactly this producer-shape ambiguity: `'data_as_of' in ps` (BL-4/
    DE-AUDIT-BL4-001, line 992) -- unconditionally set by every real /api/state
    poll's _compute_portfolio_strip (app.py) and NEVER set by
    analytics.compute_windowed_portfolio_strip (the windowed-strip producer).
  - BL4-001 wired that discriminator to the account-basis CHIP helpers only
    (renderAccountBasisChip / renderAccountBasisFreshness, lines 992-996) --
    never to the comparison rows' VALUES.
  - fetchWindowedStrip (line 1472) wraps /api/strip/<window>'s response (never
    carrying data_as_of) and calls updateComparisonRows(wrapped) (line 1481-
    1482) -- reaching this same unconditional sourcing and overwriting a
    row with a VW-basis windowed value (analytics.py:1998-2000 documents the
    windowed strip is deliberately VW/cash-excluded basis).

REVISE ROUND (PR #136 /code-review, finding #1 -- "most serious"): the first
pass gated only the 'cumulative' row. The reviewer traced that when
guard_alpha is genuinely null (sparse shadow_history / new symphony / an
except-fallback), the SAME fallback still rewrites the 'today' and 'mdd'
rows from the windowed strip too -- the identical bounce class on the two
rows the first pass left unguarded. This file's single-row test is
generalized to all three row ids (today/cumulative/mdd) accordingly.

This is a DIFFERENT bug from F-014 (DE-DISPLAY-TRUTH-001, see
tests/dashboard/test_display_truth_cluster_js.py's TestF014CumulativeLifetime
RowSourcesLifetimeValue): F-014 removed an explicit `ps.windowed_cumulative_
return ||` PREFERENCE inside the cumulative row entry (already fixed, gone
from current code, and its own test still passes). This bug is a DIFFERENT
unguarded CALLER PATH (renderGuardAlpha's fallback) reaching updateComparison
Rows' still-unconditional per-row sourcing. F-014's test requires the literal
`ps.cumulative_return` reference to stay inside the 'cumulative' row entry --
this test's fix must coexist with that constraint, not relocate the
reference to an external helper.

NECESSARY-NOT-SUFFICIENT (mirrors test_display_truth_cluster_js.py's own
documented limitation -- this repo has no JS execution harness): a
source-text pin proves a discriminator construct exists, not that the DOM
genuinely never bounces at runtime. The full behavioral proof is bounce-ux's
live Playwright DOM-sampling gate against a real browser session.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_INDEX_JS = _STATIC_DIR / "index.js"

_DISCRIMINATOR_RE = re.compile(r"data_as_of|\bbasis\b")

_ROW_SOURCE_FIELDS = {
    "today": "ps.today_change",
    "cumulative": "ps.cumulative_return",
    "mdd": "ps.max_drawdown",
}


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


def _slice_row_entry(body: str, row_id: str) -> str:
    """Extracts the FULL `{ id: '<row_id>', ... }` object-literal entry via
    brace counting from its OWN opening brace -- found by scanning BACKWARD
    from the `id: '<row_id>'` anchor to the nearest unmatched '{', then
    forward via _brace_match_end to the matching close.

    CODE-REVIEW FIX (PR #136 revise round, finding #5): the prior version
    used a naive `body.find('},', row_anchor)` forward scan, which is fooled
    two ways -- (a) it can stop EARLY at an empty-object fallback inside the
    entry itself (e.g. `values: ps.foo || {},`, where the `{},` inside the
    fallback matches before the entry's real close), and (b) if the entry's
    shape ever changes to lack an early '},' occurrence, it OVER-SPANS past
    the entry's real boundary into whatever code follows (e.g. rows.forEach's
    body) -- letting a stray data_as_of/basis token anywhere in that trailing
    code spuriously satisfy the discriminator check below. Proper
    bidirectional brace-matching is immune to both failure modes regardless
    of the entry's internal content or what follows it.
    """
    anchor_idx = body.find(f"id: '{row_id}'")
    assert anchor_idx != -1, f"id: '{row_id}' row entry not found in updateComparisonRows"
    depth = 0
    i = anchor_idx
    open_idx = -1
    while i >= 0:
        if body[i] == "}":
            depth += 1
        elif body[i] == "{":
            if depth == 0:
                open_idx = i
                break
            depth -= 1
        i -= 1
    assert open_idx != -1, f"could not find the opening brace for id: '{row_id}' entry"
    close_idx = _brace_match_end(body, open_idx)
    return body[open_idx : close_idx + 1]


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


class TestComparisonRowsGuardedByRealStatePollDiscriminator:
    """Code-review revise round (PR #136, finding #1 -- "most serious"): the
    reviewer traced that when guard_alpha is genuinely null, renderGuardAlpha's
    fallback still rewrites ALL THREE comparison rows from the windowed
    strip, not just 'cumulative' -- the identical bounce class on the two
    rows the first pass left unguarded. Generalizes the original single-row
    test to all three row ids.
    """

    @pytest.mark.parametrize("row_id", ["today", "cumulative", "mdd"])
    def test_row_values_gated_by_a_real_state_poll_discriminator(self, row_id):
        """RED (today/mdd, this revise round) / regression guard (cumulative,
        already fixed the prior round): each comparison row entry must be
        conditioned on a real-poll discriminator (data_as_of/basis), inline
        or via an enclosing guard -- not sourced unconditionally, which lets
        fetchWindowedStrip's payload (never carrying data_as_of) overwrite it
        with a windowed value. Deliberately does NOT pin one exact code shape
        (inline ternary, per-row conditional insert, or an outer if wrapping
        the whole rows construction) -- implementer discretion on mechanism,
        invariant only on the outcome, per team-lead's original Gate-2 steer.
        """
        content = _read_index_js()
        body = _slice_function(content, "function updateComparisonRows(")

        row_entry = _slice_row_entry(body, row_id)
        source_field = _ROW_SOURCE_FIELDS[row_id]

        assert source_field in row_entry, (
            f"the '{row_id}' row entry no longer sources {source_field} at all -- "
            f"row entry: {row_entry!r}."
        )

        row_anchor = body.find(f"id: '{row_id}'")
        inline_guarded = bool(_DISCRIMINATOR_RE.search(row_entry))
        wrapped_guarded = _guarded_by_wrapping_if(body, row_anchor)

        assert inline_guarded or wrapped_guarded, (
            f"FAIL: the '{row_id}' row entry sources {source_field} with no "
            f"discriminator (data_as_of / basis) conditioning it, either inline "
            f"within the entry or via an enclosing if-block -- row entry: "
            f"{row_entry!r}. This is the unconditional-sourcing bug that lets "
            f"fetchWindowedStrip's payload (which never carries data_as_of) "
            f"overwrite this row with a windowed value on every closed-market "
            f"poll (DE-CLOSED-BOUNCE-001, revise round -- gate ALL 3 rows, not "
            f"just 'cumulative')."
        )
