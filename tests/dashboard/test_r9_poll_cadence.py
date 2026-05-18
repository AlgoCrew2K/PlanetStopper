"""
RED tests for R9 — MED-7: fetchState poll cadence 5s → 15s.

Audit finding: setInterval(fetchState, 5000) at templates/index.html:1981 triples
/api/state load vs. the project's ≥15s poll-floor convention.

Fix: change 5000 → 15000 and add a comment referencing the poll-floor convention.

Acceptance criteria:
  AC-1  : setInterval(fetchState, ...) is called with 15000 ms (not 5000).
  AC-2  : A comment near the setInterval line references the poll-floor cadence rationale.
  AC-3  : No other setInterval(fetchState, ...) call with a sub-15s interval exists.

Fixture provenance (PA-18):
  templates/index.html — the production template; tests parse it directly.
  No inline construction of HTML strings.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Fixture: production template file
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = (
    pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"
)

_POLL_FLOOR_MS = 15_000  # project convention: dashboard polls must be ≥ 15 s


def _template_text() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-1: setInterval(fetchState, ...) uses 15000 ms
# ---------------------------------------------------------------------------

class TestFetchStatePollCadence:
    """
    AC-1: The setInterval that drives fetchState must fire every 15 000 ms.
    The previous value (5 000 ms) tripled /api/state load vs. spec.
    """

    def test_fetch_state_interval_is_15000_ms(self):
        """
        AC-1: setInterval(fetchState, ...) must contain the literal 15000.
        Fails RED while the template still reads setInterval(fetchState, 5000).
        """
        html = _template_text()

        # Match setInterval(fetchState, <number>) with optional whitespace
        matches = re.findall(
            r"setInterval\s*\(\s*fetchState\s*,\s*(\d+)\s*\)",
            html,
        )

        assert matches, (
            "No setInterval(fetchState, ...) call found in templates/index.html. "
            "Expected exactly one such call — implementer must not have removed it."
        )

        intervals_ms = [int(v) for v in matches]
        assert all(v == _POLL_FLOOR_MS for v in intervals_ms), (
            f"setInterval(fetchState, ...) must use {_POLL_FLOOR_MS} ms "
            f"(project poll-floor convention). "
            f"Found: {intervals_ms}. "
            "MED-7 fix: change 5000 → 15000."
        )

    def test_fetch_state_interval_is_not_5000_ms(self):
        """
        AC-1 (negative): The stale 5 000 ms value must NOT be present in any
        setInterval(fetchState, ...) call after the fix.
        Fails RED while the template still contains the old value.
        """
        html = _template_text()

        matches = re.findall(
            r"setInterval\s*\(\s*fetchState\s*,\s*5000\s*\)",
            html,
        )

        assert not matches, (
            "Found setInterval(fetchState, 5000) in templates/index.html — "
            "this is the pre-fix value. "
            "MED-7: change 5000 → 15000 so the poll respects the ≥15s floor convention."
        )


# ---------------------------------------------------------------------------
# AC-2: explanatory comment present near the setInterval line
# ---------------------------------------------------------------------------

class TestPollCadenceCommentPresent:
    """
    AC-2: A comment near the setInterval(fetchState, ...) call must reference
    the poll-floor convention so future readers understand the constraint.
    """

    def test_poll_floor_comment_near_fetch_state_interval(self):
        """
        AC-2: A JS comment (// or /* ... */) within 3 lines of the
        setInterval(fetchState, ...) call must mention 'poll' and ('floor' or
        'cadence' or 'convention' or '15s' or '15 s').
        Fails RED if no such comment exists yet.
        """
        html = _template_text()
        lines = html.splitlines()

        # Find the line index of setInterval(fetchState, ...)
        interval_line_idx: int | None = None
        for idx, line in enumerate(lines):
            if re.search(
                r"setInterval\s*\(\s*fetchState\s*,\s*\d+\s*\)", line
            ):
                interval_line_idx = idx
                break

        assert interval_line_idx is not None, (
            "setInterval(fetchState, ...) line not found in templates/index.html. "
            "Cannot check for poll-floor comment."
        )

        # Check the 3 lines before and 3 lines after for a comment referencing the floor
        window_start = max(0, interval_line_idx - 3)
        window_end = min(len(lines), interval_line_idx + 4)
        window = lines[window_start:window_end]

        comment_pattern = re.compile(
            r"(?://|/\*).*poll.*(floor|cadence|convention|15s|15 s)",
            re.IGNORECASE,
        )
        found_comment = any(comment_pattern.search(line) for line in window)

        assert found_comment, (
            f"No poll-floor comment found within 3 lines of setInterval(fetchState, ...) "
            f"at line {interval_line_idx + 1}. "
            "Implementer must add a JS comment (e.g. '// poll floor: ≥15s convention') "
            "near the setInterval call. "
            f"Window checked ({window_start + 1}–{window_end}):\n"
            + "\n".join(f"  {window_start + i + 1}: {l}" for i, l in enumerate(window))
        )


# ---------------------------------------------------------------------------
# AC-3: no other sub-floor setInterval(fetchState, ...) calls
# ---------------------------------------------------------------------------

class TestNoSubFloorFetchStateInterval:
    """
    AC-3: Ensure there is no second setInterval(fetchState, ...) call anywhere
    in the template with an interval below the 15 s floor.
    Guards against a duplicate call being introduced alongside the fixed one.
    """

    def test_no_other_sub_floor_fetch_state_interval_exists(self):
        """
        AC-3: All setInterval(fetchState, ...) calls must use ≥ 15 000 ms.
        Fails RED if any sub-floor value (including 5000) is present anywhere.
        """
        html = _template_text()

        all_matches = re.findall(
            r"setInterval\s*\(\s*fetchState\s*,\s*(\d+)\s*\)",
            html,
        )

        sub_floor = [int(v) for v in all_matches if int(v) < _POLL_FLOOR_MS]
        assert not sub_floor, (
            f"Found setInterval(fetchState, ...) with sub-floor interval(s): {sub_floor} ms. "
            f"All fetchState polls must be ≥ {_POLL_FLOOR_MS} ms per project convention."
        )
