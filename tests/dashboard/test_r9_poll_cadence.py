"""
Tests for R9 — MED-7: fetchState poll cadence.

Polling moved from templates/index.html to static/index.js and was renamed
from fetchState to loadState. The interval was set to 30 000 ms via the
named constant POLL_INTERVAL_MS (not a magic number). This is the correct
cadence per the behavior audit (B-27 confirmed 30s works; C-19 triage).

Acceptance criteria (updated for current contract):
  AC-1  : setInterval(loadState, ...) is called via POLL_INTERVAL_MS = 30000.
  AC-2  : A comment near the setInterval line references the poll cadence.
  AC-3  : No other setInterval(loadState, ...) call with a sub-15s interval exists.

Fixture provenance (PA-18):
  static/index.js — the production JS file; tests parse it directly.
"""

from __future__ import annotations

import pathlib
import re

# ---------------------------------------------------------------------------
# Fixture: production JS file
# ---------------------------------------------------------------------------

_JS_PATH = pathlib.Path(__file__).parent.parent.parent / "static" / "index.js"

_POLL_FLOOR_MS = 15_000   # project minimum; actual value is 30 000 ms
_POLL_ACTUAL_MS = 30_000  # confirmed by behavior audit B-27


def _js_text() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-1: setInterval(loadState, ...) uses POLL_INTERVAL_MS constant
# ---------------------------------------------------------------------------


class TestFetchStatePollCadence:
    """
    AC-1: The setInterval that drives loadState must fire every 30 000 ms
    via the named constant POLL_INTERVAL_MS.
    """

    def test_fetch_state_interval_is_15000_ms(self):
        """
        AC-1: POLL_INTERVAL_MS must be >= 15 000 ms.
        The actual value is 30 000 ms per the behavior audit (B-27).
        Polling is in static/index.js, not templates/index.html (C-19 fix).
        """
        src = _js_text()

        # The named constant must be present and >= poll floor
        const_matches = re.findall(
            r"var\s+POLL_INTERVAL_MS\s*=\s*(\d+)",
            src,
        )

        assert const_matches, (
            "No POLL_INTERVAL_MS constant found in static/index.js. "
            "Expected: var POLL_INTERVAL_MS = 30000;"
        )

        for v in const_matches:
            assert int(v) >= _POLL_FLOOR_MS, (
                f"POLL_INTERVAL_MS must be >= {_POLL_FLOOR_MS} ms "
                f"(project poll-floor convention). Found: {v} ms."
            )

        # setInterval must use the named constant
        assert re.search(r"setInterval\s*\(\s*loadState\s*,\s*POLL_INTERVAL_MS\s*\)", src), (
            "static/index.js must call setInterval(loadState, POLL_INTERVAL_MS). "
            "Magic number intervals are disallowed per project rules."
        )

    def test_fetch_state_interval_is_not_5000_ms(self):
        """
        AC-1 (negative): A bare 5 000 ms literal must NOT be in any
        setInterval(loadState, ...) call.
        """
        src = _js_text()

        matches = re.findall(
            r"setInterval\s*\(\s*loadState\s*,\s*5000\s*\)",
            src,
        )

        assert not matches, (
            "Found setInterval(loadState, 5000) in static/index.js — "
            "use the named constant POLL_INTERVAL_MS instead."
        )


# ---------------------------------------------------------------------------
# AC-2: explanatory comment present near the setInterval line
# ---------------------------------------------------------------------------


class TestPollCadenceCommentPresent:
    """
    AC-2: A comment near the setInterval(loadState, ...) call must reference
    the poll cadence so future readers understand the constraint.
    """

    def test_poll_floor_comment_near_fetch_state_interval(self):
        """
        AC-2: A JS comment (// or /* ... */) within 3 lines of the
        setInterval(loadState, ...) call must mention 'poll'.
        """
        src = _js_text()
        lines = src.splitlines()

        interval_line_idx: int | None = None
        for idx, line in enumerate(lines):
            if re.search(r"setInterval\s*\(\s*loadState\s*,\s*POLL_INTERVAL_MS\s*\)", line):
                interval_line_idx = idx
                break

        assert interval_line_idx is not None, (
            "setInterval(loadState, POLL_INTERVAL_MS) line not found in static/index.js."
        )

        window_start = max(0, interval_line_idx - 15)
        window_end = min(len(lines), interval_line_idx + 4)
        window = lines[window_start:window_end]

        comment_pattern = re.compile(
            r"(?://|/\*).*poll",
            re.IGNORECASE,
        )
        found_comment = any(comment_pattern.search(line) for line in window)

        assert found_comment, (
            f"No poll comment found within 15 lines of setInterval(loadState, POLL_INTERVAL_MS) "
            f"at line {interval_line_idx + 1}. "
            "Add a JS comment mentioning 'poll' near the setInterval call. "
            f"Window checked ({window_start + 1}–{window_end}):\n"
            + "\n".join(f"  {window_start + i + 1}: {l}" for i, l in enumerate(window))
        )


# ---------------------------------------------------------------------------
# AC-3: no other sub-floor setInterval(loadState, ...) calls
# ---------------------------------------------------------------------------


class TestNoSubFloorFetchStateInterval:
    """
    AC-3: All setInterval(loadState, ...) or setInterval(fetchState, ...) calls
    must use >= 15 000 ms.
    """

    def test_no_other_sub_floor_fetch_state_interval_exists(self):
        """
        AC-3: All loadState/fetchState poll intervals must be >= 15 000 ms.
        """
        src = _js_text()

        all_matches = re.findall(
            r"setInterval\s*\(\s*(?:loadState|fetchState)\s*,\s*(\d+)\s*\)",
            src,
        )

        sub_floor = [int(v) for v in all_matches if int(v) < _POLL_FLOOR_MS]
        assert not sub_floor, (
            f"Found setInterval(loadState/fetchState, ...) with sub-floor interval(s): "
            f"{sub_floor} ms. All state polls must be >= {_POLL_FLOOR_MS} ms."
        )
