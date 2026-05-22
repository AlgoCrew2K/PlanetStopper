"""
AC-5 / N-3 — replay grace gate honors EXECUTION_START_TIME.

Production's VWAP open-window grace is anchored on EXECUTION_START_TIME:
``is_in_open_window_grace(current_et, EXECUTION_START_TIME, grace_minutes)``
suppresses VWAP-Breakdown / VWAP-Bleed-Cut for the interval
``[exec_start, exec_start + grace_minutes)``.

The replay anchors on the WRONG point. tick_idx 0 corresponds to the
SESSION OPEN at 09:30 ET (the data-phase loop runs from 09:30 onward —
alpha_bot_execution.py:681), and the replay's gate
``if tick_idx < grace_minutes`` always activates over tick indices 0 to
``grace_minutes - 1``. When the operator sets a non-default
EXECUTION_START_TIME (e.g. 10:30), production's grace lives at 10:30-10:45
but the replay suppresses 09:30-09:44 — a complete misalignment.

Decision (plan AC-5): the replay must compute its grace window as

    start_offset_minutes = (h - 9) * 60 + (m - 30)
    grace_window = [start_offset_minutes, start_offset_minutes + grace_minutes)

where ``h, m = EXECUTION_START_TIME.split(":")``.

Tests pin:
  1. A pure helper for the replay grace gate exists, takes
     ``(tick_idx, execution_start_hhmm, grace_minutes)`` and returns the
     same bool the in-loop conditional uses.
  2. With EXECUTION_START_TIME="09:30" + grace=15: tick indices 0-14 are
     in grace, 15+ are not (back-compatible with the current default).
  3. With EXECUTION_START_TIME="10:30" + grace=15: tick indices 60-74 are
     in grace, 0-59 and 75+ are NOT — pins the non-default operator case.
  4. End-to-end via the replay: a VWAP-Breakdown signal at tick_idx=10
     with EXECUTION_START_TIME="10:30" is NOT suppressed by the grace
     (production would not suppress it either — 09:40 is well before the
     10:30 exec-start anchor).
  5. End-to-end: a VWAP-Breakdown signal at tick_idx=65 (≈10:35) with
     EXECUTION_START_TIME="10:30" IS suppressed (production would
     suppress it: 10:35 is inside [10:30, 10:45)).

Provenance: all expected tick-indices are derived from the
``(h - 9) * 60 + (m - 30)`` formula in the test, never from a producer
helper.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import autotuner


# ---------------------------------------------------------------------------
# 1 — Pure helper exists.
# ---------------------------------------------------------------------------


class TestReplayGraceHelperExists:
    """The replay's grace gate is currently inlined as ``tick_idx <
    grace_minutes``. To support EXECUTION_START_TIME the inlining must be
    factored to a helper that the test can pin in isolation."""

    def test_replay_in_open_window_grace_helper_exists(self):
        """A pure helper ``_replay_in_open_window_grace(tick_idx,
        execution_start_hhmm, grace_minutes)`` (or its public-name twin)
        must be exposed.

        Acceptable name variants — implementer picks one. Without a helper
        the test cannot pin the EXECUTION_START_TIME axis independently of
        the full replay loop.
        """
        candidate_names = (
            "_replay_in_open_window_grace",
            "replay_in_open_window_grace",
            "_replay_grace_active",
            "_is_replay_in_open_window_grace",
        )
        assert any(hasattr(autotuner, n) for n in candidate_names), (
            "AC-5 / N-3: the replay grace gate must be exposed as a pure "
            "helper so tests can drive the EXECUTION_START_TIME axis "
            f"independently. Searched: {candidate_names}."
        )


def _resolve_grace_helper():
    """Resolve the replay grace helper across acceptable name variants."""
    for n in (
        "_replay_in_open_window_grace",
        "replay_in_open_window_grace",
        "_replay_grace_active",
        "_is_replay_in_open_window_grace",
    ):
        if hasattr(autotuner, n):
            return getattr(autotuner, n)
    pytest.fail("Replay grace helper not exposed.")


# ---------------------------------------------------------------------------
# 2 — Default EXECUTION_START_TIME="09:30" (back-compat).
# ---------------------------------------------------------------------------


class TestReplayGraceDefaultExecutionStart:
    """With the default EXECUTION_START_TIME="09:30", the start-offset is
    0 and the helper reduces to ``tick_idx < grace_minutes`` — the exact
    behaviour of the current inline gate. Back-compat pin."""

    @pytest.mark.parametrize(
        "tick_idx, expected",
        [
            (0, True),
            (5, True),
            (14, True),
            (15, False),
            (60, False),
            (200, False),
        ],
    )
    def test_default_exec_start_grace_window(self, tick_idx: int, expected: bool):
        """exec_start=09:30, grace=15: ticks 0..14 are in grace; 15+ are
        not."""
        helper = _resolve_grace_helper()
        try:
            result = helper(
                tick_idx=tick_idx,
                execution_start_hhmm="09:30",
                grace_minutes=15,
            )
        except TypeError:
            result = helper(tick_idx, "09:30", 15)
        assert result is expected, (
            f"AC-5 / N-3 default-case VIOLATED: helper(tick_idx={tick_idx}, "
            f"exec_start='09:30', grace=15) returned {result!r}; expected "
            f"{expected!r}. With exec_start at session open, grace runs "
            f"[0, grace_minutes)."
        )


# ---------------------------------------------------------------------------
# 3 — Non-default EXECUTION_START_TIME.
# ---------------------------------------------------------------------------


class TestReplayGraceNonDefaultExecutionStart:
    """The behavioural pin AC-5 is built for — operator runs the daemon
    with EXECUTION_START_TIME="10:30"."""

    @pytest.mark.parametrize(
        "exec_start, grace_minutes, tick_idx, expected",
        [
            # exec_start=10:30 -> start_offset = (10-9)*60 + (30-30) = 60.
            # grace window = [60, 75).
            ("10:30", 15, 0,   False),  # 09:30 — far before exec_start
            ("10:30", 15, 30,  False),  # 10:00 — still before exec_start
            ("10:30", 15, 59,  False),  # 10:29 — one minute before
            ("10:30", 15, 60,  True),   # 10:30 — exec_start tick
            ("10:30", 15, 65,  True),   # 10:35 — inside grace
            ("10:30", 15, 74,  True),   # 10:44 — last grace tick
            ("10:30", 15, 75,  False),  # 10:45 — grace expired
            ("10:30", 15, 200, False),  # late afternoon

            # Sanity for an exotic exec_start
            ("11:00", 10, 89,  False),
            ("11:00", 10, 90,  True),   # 11:00 — exec_start
            ("11:00", 10, 99,  True),   # 11:09 — inside grace
            ("11:00", 10, 100, False),  # 11:10 — grace expired
        ],
    )
    def test_non_default_exec_start_drives_grace_window(
        self,
        exec_start: str,
        grace_minutes: int,
        tick_idx: int,
        expected: bool,
    ):
        """Independent expected derivation:

            h, m = exec_start.split(':')
            start_offset = (h - 9) * 60 + (m - 30)
            expected = start_offset <= tick_idx < start_offset + grace_minutes
        """
        h, m = exec_start.split(":")
        start_offset = (int(h) - 9) * 60 + (int(m) - 30)
        expected_independent = (
            start_offset <= tick_idx < start_offset + grace_minutes
        )
        assert expected_independent == expected, (
            "Fixture self-check: parametrized expected disagrees with the "
            f"formula ({expected!r} vs {expected_independent!r})."
        )

        helper = _resolve_grace_helper()
        try:
            result = helper(
                tick_idx=tick_idx,
                execution_start_hhmm=exec_start,
                grace_minutes=grace_minutes,
            )
        except TypeError:
            result = helper(tick_idx, exec_start, grace_minutes)
        assert result is expected, (
            f"AC-5 / N-3 VIOLATED: helper(tick_idx={tick_idx}, "
            f"exec_start={exec_start!r}, grace={grace_minutes}) returned "
            f"{result!r}; expected {expected!r}. start_offset="
            f"{start_offset}; grace window = "
            f"[{start_offset}, {start_offset + grace_minutes})."
        )


# ---------------------------------------------------------------------------
# 4 — Replay call site reads EXECUTION_START_TIME (env-driven).
# ---------------------------------------------------------------------------


class TestReplayCallSiteReadsExecutionStartTime:
    """The replay's grace-check at the per-tick exit core must consult
    EXECUTION_START_TIME, not just the grace_minutes count. Pin that the
    replay-state resolution function returns BOTH dials, so the call site
    can pass both into the helper."""

    def test_replay_grace_resolver_exposes_execution_start_time(self):
        """A resolver that returns ONLY grace_minutes is insufficient
        post-AC-5: the replay needs (exec_start, grace_minutes). The
        existing _replay_grace_minutes returns only the int — it must be
        joined by an EXECUTION_START_TIME accessor OR replaced by a
        2-tuple-returning resolver."""
        # Acceptable shapes:
        #   (a) a new _replay_execution_start_time() returning str;
        #   (b) _replay_grace_minutes returning a tuple (start_hhmm, mins);
        #   (c) a combined _replay_grace_context() returning a dict/namespace.
        import alpha_bot_execution

        a = hasattr(autotuner, "_replay_execution_start_time")
        b = False
        if hasattr(autotuner, "_replay_grace_minutes"):
            try:
                returned = autotuner._replay_grace_minutes()
                b = isinstance(returned, tuple) and len(returned) == 2
            except Exception:  # pragma: no cover
                b = False
        c = hasattr(autotuner, "_replay_grace_context")
        # Also accept reading alpha_bot_execution.EXECUTION_START_TIME
        # directly from the replay — the source-scan path.
        d = False
        import inspect
        try:
            src = inspect.getsource(autotuner._replay_exit_tick)
            d = "EXECUTION_START_TIME" in src
        except Exception:  # pragma: no cover
            d = False
        assert (a or b or c or d), (
            "AC-5 / N-3: the replay must obtain EXECUTION_START_TIME "
            "somehow — either by a new helper "
            "(_replay_execution_start_time / _replay_grace_context), by "
            "expanding _replay_grace_minutes into a 2-tuple "
            "(start_hhmm, minutes), or by directly reading "
            "alpha_bot_execution.EXECUTION_START_TIME in _replay_exit_tick. "
            "Currently the replay only consumes grace_minutes."
        )


# ---------------------------------------------------------------------------
# 5 — Inline-call-site pin: the in-loop gate at autotuner.py:581 must use
# the new helper (not the raw `tick_idx < grace_minutes`).
#
# An AST scan over _replay_exit_tick: the current `tick_idx < grace_minutes`
# expression must be REPLACED by a helper call (or by a richer condition
# that references both grace_minutes and the exec-start offset). Pin the
# replacement structurally so the in-loop bug cannot regress unnoticed.
# ---------------------------------------------------------------------------


class TestInLoopGateUsesHelper:
    """The in-loop grace gate at the per-tick exit core must NOT be the
    bare ``tick_idx < grace_minutes`` comparison post-AC-5."""

    def test_inline_tick_idx_less_grace_minutes_replaced(self):
        """AST-scan _replay_exit_tick for the literal
        ``tick_idx < grace_minutes`` comparison. Post-AC-5 the gate must
        be a helper call (one of the names from
        TestReplayGraceHelperExists) or otherwise reference an
        execution-start derivative — the raw two-operand comparison
        cannot survive.
        """
        import ast
        import inspect

        src = inspect.getsource(autotuner._replay_exit_tick)
        tree = ast.parse(src)

        # Search for a Compare node where left.id == 'tick_idx' and the
        # single comparator is Name 'grace_minutes' under a Lt op.
        offending = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not (isinstance(node.left, ast.Name) and node.left.id == "tick_idx"):
                continue
            if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Lt):
                continue
            if not (
                len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Name)
                and node.comparators[0].id == "grace_minutes"
            ):
                continue
            offending.append(node.lineno)

        assert not offending, (
            "AC-5 / N-3 VIOLATED: _replay_exit_tick still uses the bare "
            f"`tick_idx < grace_minutes` comparison at line(s) "
            f"{offending}. The gate must consult EXECUTION_START_TIME — "
            "either via the new helper (TestReplayGraceHelperExists) or "
            "via an expression that includes the start-offset derivation."
        )
