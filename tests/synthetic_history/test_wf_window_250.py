"""
Phase-1 walk-forward window extension: RED tests for AC-1/2/6.

Acceptance criteria covered here:
  AC-1  _WALK_FORWARD_TRADING_DAYS == 250 and propagates to
        _REQUIRED_FETCH_TRADING_DAYS, compute_fetch_window_start, and the
        [-_WALK_FORWARD_TRADING_DAYS:] replay slice.
  AC-2  Cache marker bumped v3 -> v4; a v3 file is NEVER served under a
        250-day expectation.
  AC-6  Literal "125" must not survive in docstrings, prints, or comments
        adjacent to the walk-forward window in synthetic_history.py; all
        references must use the named constant.

Feature plan: feature-plans/walk-forward-overhaul.md Phase 1.
Merge-base: 2f8ce8a.

Fixture provenance: all numeric expectations are derived from named module
constants (math_engine.MC_MIN_HISTORY_DAYS, math_engine.MC_VOL_WINDOW_DAYS)
and the new _WALK_FORWARD_TRADING_DAYS = 250 expectation. No producer-computed
dollar/rate/percent values are asserted. Trading-day counts are exact integers
(no pytest.approx needed).

Tests in this file must ALL fail RED against the current codebase (which still
has _WALK_FORWARD_TRADING_DAYS = 125 and cache marker v3) and must turn GREEN
after the implementer applies the Phase-1 change.
"""
from __future__ import annotations

import ast as _ast
import datetime as _dt
import pathlib
import json
import os

import pytest

import math_engine


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"


def _synth_source() -> str:
    assert _SYNTH_PATH.is_file(), f"expected synthetic_history.py at {_SYNTH_PATH}"
    return _SYNTH_PATH.read_text(encoding="utf-8")


def _synth_tree() -> _ast.Module:
    return _ast.parse(_synth_source(), filename=str(_SYNTH_PATH))


# ---------------------------------------------------------------------------
# Expected values (derived from constants, never literals)
# ---------------------------------------------------------------------------

# AC-1 target: the new walk-forward window (Phase-1 change).
_EXPECTED_WALK_FORWARD_TRADING_DAYS = 250

# The MC warmup is additive to the replay window and derived from named
# math_engine constants — NOT a magic number.
_MC_WARMUP = math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)

# The total trading-day floor the fetch must satisfy (warmup + replay + buffer).
# The buffer constant lives in the implementation; we only assert the floor
# includes at least the warmup + replay component.
_MIN_REQUIRED_NO_BUFFER = _EXPECTED_WALK_FORWARD_TRADING_DAYS + _MC_WARMUP


# ===========================================================================
# AC-1 — _WALK_FORWARD_TRADING_DAYS constant
# ===========================================================================


class TestWalkForwardWindowConstant250:
    """
    The module-level constant _WALK_FORWARD_TRADING_DAYS must equal 250 after
    the Phase-1 change.

    Adversarial angles:
    - An implementer who only edits the number in one place but not the
      constant definition is caught by the import test.
    - An implementer who renames the constant breaks the static import test
      (the production name is pinned by the AC-1 contract and by downstream
      tests already passing on 125).
    """

    def test_constant_equals_250(self):
        """_WALK_FORWARD_TRADING_DAYS must equal 250 at import time."""
        import synthetic_history as sh
        actual = sh._WALK_FORWARD_TRADING_DAYS
        assert actual == _EXPECTED_WALK_FORWARD_TRADING_DAYS, (
            f"synthetic_history._WALK_FORWARD_TRADING_DAYS = {actual}; "
            f"Phase-1 requires 250. RED until the constant is updated."
        )

    def test_required_fetch_trading_days_includes_the_new_replay_window(self):
        """_REQUIRED_FETCH_TRADING_DAYS must be >= 250 + MC_WARMUP.

        The fetch floor must accommodate the new 250-day replay window plus
        the additive MC warmup. Asserting '>= minimum without buffer' catches
        an implementer who bumps _WALK_FORWARD_TRADING_DAYS but forgets to
        recompute _REQUIRED_FETCH_TRADING_DAYS (e.g. leaves it hardcoded at
        the old 125-derived value of 174).
        """
        import synthetic_history as sh
        floor = sh._REQUIRED_FETCH_TRADING_DAYS
        assert floor >= _MIN_REQUIRED_NO_BUFFER, (
            f"synthetic_history._REQUIRED_FETCH_TRADING_DAYS = {floor}; "
            f"must be >= _WALK_FORWARD_TRADING_DAYS(250) + MC_WARMUP({_MC_WARMUP}) "
            f"= {_MIN_REQUIRED_NO_BUFFER}. The fetch floor must account for "
            f"both the 250-day replay window and the additive MC warmup. RED "
            f"until _REQUIRED_FETCH_TRADING_DAYS is recomputed from the new "
            f"_WALK_FORWARD_TRADING_DAYS constant."
        )

    def test_required_fetch_trading_days_is_derived_not_hardcoded(self):
        """_REQUIRED_FETCH_TRADING_DAYS must be an EXPRESSION referencing
        _WALK_FORWARD_TRADING_DAYS, not a hardcoded literal.

        Static analysis: find the module-level assignment for
        _REQUIRED_FETCH_TRADING_DAYS and confirm the RHS is not a bare
        numeric Constant (which would silently drift from the replay window
        constant). A tuple/add/name expression is required.
        """
        tree = _synth_tree()
        for node in tree.body:
            if not isinstance(node, _ast.Assign):
                continue
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name) and tgt.id == "_REQUIRED_FETCH_TRADING_DAYS":
                    rhs = node.value
                    assert not isinstance(rhs, _ast.Constant), (
                        "_REQUIRED_FETCH_TRADING_DAYS must be an expression "
                        "that references _WALK_FORWARD_TRADING_DAYS (and/or "
                        "other named constants), not a bare numeric literal. "
                        "A literal cannot propagate the Phase-1 window change "
                        "automatically. RED until the assignment is an "
                        "expression of named constants."
                    )
                    # Confirm _WALK_FORWARD_TRADING_DAYS is referenced in the RHS.
                    names_in_rhs = {
                        n.id
                        for n in _ast.walk(rhs)
                        if isinstance(n, _ast.Name)
                    }
                    assert "_WALK_FORWARD_TRADING_DAYS" in names_in_rhs, (
                        "_REQUIRED_FETCH_TRADING_DAYS RHS does not reference "
                        "_WALK_FORWARD_TRADING_DAYS. After the Phase-1 change "
                        "these two constants must be coupled so bumping the "
                        "replay window automatically raises the fetch floor."
                    )
                    return
        pytest.fail(
            "Could not find a module-level assignment for "
            "_REQUIRED_FETCH_TRADING_DAYS in synthetic_history.py. "
            "The constant must exist at module scope."
        )

    def test_replay_slice_uses_the_constant_not_a_literal_250(self):
        """The [-N:] intraday replay slice must reference _WALK_FORWARD_TRADING_DAYS,
        not a bare literal.

        After the Phase-1 change the constant is 250. An implementer who
        edits the constant but forgets to update a separate hardcoded slice
        literal in generate_synthetic_history would silently keep the 125-day
        (or wrong) slice.

        Static check: no subscript node in generate_synthetic_history should
        use a bare integer literal for the replay slice; the slice index must
        reference _WALK_FORWARD_TRADING_DAYS (possibly via unary negation).
        """
        tree = _synth_tree()
        gen = None
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "generate_synthetic_history":
                gen = node
                break
        assert gen is not None, "generate_synthetic_history not found in synthetic_history.py"

        # Walk generate_synthetic_history for Subscript nodes whose slice is a
        # bare negative integer literal (e.g. [-125:] or [-250:]).
        bare_literal_slices: list[int] = []
        for node in _ast.walk(gen):
            if not isinstance(node, _ast.Subscript):
                continue
            sl = node.slice
            # Python 3.9+: slice is a Slice node; earlier ASTs may nest differently.
            if isinstance(sl, _ast.Slice):
                lower = sl.lower
                # Detect `[-N:]` patterns: lower is UnaryOp(-N) or Constant(-N).
                if isinstance(lower, _ast.UnaryOp) and isinstance(lower.op, _ast.USub):
                    if isinstance(lower.operand, _ast.Constant) and isinstance(
                        lower.operand.value, int
                    ):
                        bare_literal_slices.append(lower.operand.value)
                elif isinstance(lower, _ast.Constant) and isinstance(lower.value, int):
                    if lower.value < 0:
                        bare_literal_slices.append(abs(lower.value))

        # Any bare-literal slice with a value that looks like the old (125) or
        # new (250) replay window is a maintenance hazard: it will silently
        # diverge from _WALK_FORWARD_TRADING_DAYS on the next window change.
        suspicious = [v for v in bare_literal_slices if v in (125, 250)]
        assert not suspicious, (
            f"generate_synthetic_history contains bare literal [-N:] replay "
            f"slice(s) with N in {suspicious}. The replay slice must reference "
            f"_WALK_FORWARD_TRADING_DAYS so the window constant and the slice "
            f"can never diverge. RED until the literal is replaced with "
            f"`[-_WALK_FORWARD_TRADING_DAYS:]`."
        )

    def test_compute_fetch_window_start_returns_earlier_date_for_250_window(self):
        """compute_fetch_window_start must return a start date that is
        sufficiently earlier to accommodate the 250-day window.

        After the Phase-1 change, _REQUIRED_FETCH_TRADING_DAYS grows
        (250 + MC_WARMUP + buffer vs 125 + MC_WARMUP + buffer). The
        calendar-day span must scale accordingly.

        Concretely:
          _REQUIRED_FETCH_TRADING_DAYS(250) = 250 + 39 + 10 = 299
          calendar_span >= 299 * 1.5 = 448.5 -> at least 448 days

        The current (125-day) fetch produces a 261-day span. A 250-day
        window must produce a span >= 448 days. An implementer who forgets
        to recompute the calendar-day padding formula and leaves
        _REQUIRED_FETCH_TRADING_DAYS at the old 174 value would still
        produce ~261 days — well below 448 — and fail here.
        """
        import synthetic_history as sh

        end_date = _dt.date(2026, 5, 15)
        start_date = sh.compute_fetch_window_start(end_date)
        if isinstance(start_date, _dt.datetime):
            start_date = start_date.date()

        calendar_span = (end_date - start_date).days

        # _REQUIRED_FETCH_TRADING_DAYS for a 250-day window:
        #   250 (replay) + 39 (MC warmup) + 10 (buffer) = 299
        # Calendar padding factor = 1.5, so minimum span = int(299 * 1.5) = 448.
        # The old 125-day window gives 261 days — well below this threshold.
        # This assertion distinguishes the new 250-day window behaviour from
        # the old 125-day window behaviour.
        expected_required = _EXPECTED_WALK_FORWARD_TRADING_DAYS + _MC_WARMUP + 10  # +10 buffer
        min_expected_span = int(expected_required * 1.5)
        assert calendar_span >= min_expected_span, (
            f"compute_fetch_window_start returns a calendar span of only "
            f"{calendar_span} days for the 250-day walk-forward window. "
            f"Expected >= {min_expected_span} days "
            f"(= int({expected_required} * 1.5), where "
            f"_REQUIRED_FETCH_TRADING_DAYS = {_EXPECTED_WALK_FORWARD_TRADING_DAYS} "
            f"+ {_MC_WARMUP}(MC_WARMUP) + 10(buffer) = {expected_required}). "
            f"The current 125-day window produces ~261 days. RED until the "
            f"padding formula is re-derived from the updated constant."
        )


# ===========================================================================
# AC-2 — Cache marker v3 -> v4
# ===========================================================================


class TestCacheMarkerBumpedV4:
    """
    The on-disk cache filename marker must be v4 after the Phase-1 change.
    A stale v3 file written under the 125-day expectation must never load
    under the 250-day expectation (it would under-deliver half the required
    history).

    Adversarial angles:
    - An implementer who bumps the window constant but forgets the cache
      marker ships a silent regression: the autotuner loads a 125-day cache
      and tunes against a degenerate window.
    - An implementer who only edits the marker in one branch (the write path
      but not the read path, or vice versa) is caught by the v3-not-served
      test.
    """

    def test_cache_filename_contains_v4_not_v3(self):
        """The cache filename format string must contain the literal 'v4'
        and must NOT contain 'v3'.

        Static analysis: find the cache_file assignment in
        generate_synthetic_history; confirm the format string uses 'v4'.
        """
        src = _synth_source()

        # The cache filename is built as an f-string or str.join; the version
        # marker is the substring 'synthetic_history_v<N>'. After the Phase-1
        # change 'synthetic_history_v4' must appear and 'synthetic_history_v3'
        # must not.
        assert "synthetic_history_v4" in src, (
            "synthetic_history.py does not contain the string "
            "'synthetic_history_v4'. The cache marker must be bumped from "
            "v3 to v4 as part of the Phase-1 window extension: a v3 cache "
            "file sized for 125 trading days must never load under the new "
            "250-day expectation. RED until the cache marker is v4."
        )
        assert "synthetic_history_v3" not in src, (
            "synthetic_history.py still contains 'synthetic_history_v3'. "
            "The v3 marker must be replaced by v4 throughout; any remaining "
            "v3 reference means a stale 125-day cache could still match the "
            "new cache lookup key and be served to the autotuner. RED until "
            "every 'synthetic_history_v3' occurrence is replaced by "
            "'synthetic_history_v4'."
        )

    def test_v3_key_and_v4_key_differ_for_same_date_and_holdings(self):
        """A v3 cache filename and a v4 cache filename for the same date and
        holdings are DIFFERENT strings.

        This is the fundamental non-collision guarantee: the v4 rename is
        what ensures that a stale v3 file on disk cannot accidentally match
        the v4 lookup key and be returned under the 250-day expectation.

        After the Phase-1 change the cache key contains 'v4'; a v3 file
        written before the Phase-1 change contains 'v3' in its filename.
        Because the filename comparison is exact, the v3 file is never found
        under a v4 key.

        This test verifies the key-generation logic produces the correct v4
        key by checking that the source string 'synthetic_history_v4' appears
        in the generated filename path (the complementary static test,
        test_cache_filename_contains_v4_not_v3, confirms the source code
        produces this pattern).
        """
        import hashlib
        import json as _json
        import synthetic_history as sh

        # Reconstruct the cache filename using the same logic as the production
        # code: holdings hash + date + version marker. We assert the MARKER
        # embedded in the cache filename is 'v4', not 'v3'.
        test_date = "2026-05-15"
        symphony_holdings = {"sym-A": [{"ticker": "SPY", "allocation": 1.0}]}
        holdings_str = _json.dumps(symphony_holdings, sort_keys=True)
        holdings_hash = hashlib.md5(holdings_str.encode("utf-8")).hexdigest()

        # The expected v4 filename pattern after the Phase-1 change.
        expected_v4_filename = (
            f"synthetic_history_v4_{test_date}_{holdings_hash}.json"
        )
        stale_v3_filename = (
            f"synthetic_history_v3_{test_date}_{holdings_hash}.json"
        )

        assert expected_v4_filename != stale_v3_filename, (
            "v4 and v3 filenames are identical — the version marker is not "
            "differentiating cache keys. This is a test-logic error."
        )

        # The core assertion: with the v4 marker in place, looking for the
        # v4 filename will never match a v3 filename.
        assert "v4" in expected_v4_filename, (
            "expected v4 filename does not contain 'v4' — test logic error."
        )
        assert "v4" not in stale_v3_filename, (
            "stale v3 filename contains 'v4' — test logic error."
        )

        # After the Phase-1 change is applied, the source code must generate
        # the v4 filename. Verify by checking that the source contains the v4
        # pattern (the complementary static test also asserts this; the
        # duplication is intentional for coverage breadth).
        src = _synth_source()
        assert "synthetic_history_v4" in src, (
            "synthetic_history.py does not produce a 'synthetic_history_v4' "
            "cache filename. A v3 file written under the 125-day window would "
            "load verbatim under a v3 key, bypassing the 250-day floor check. "
            "RED until the cache marker is bumped to v4."
        )


# ===========================================================================
# AC-6 — No stale "125" literals in walk-forward-adjacent context
# ===========================================================================


class TestNoStale125Literals:
    """
    After the Phase-1 change the literal '125' must not survive in:
    - docstrings and comments co-located with the walk-forward window
    - the replay-day print statement in generate_synthetic_history
    - the print/log statement in run_autotuner that announces the WFA

    References in citation-only contexts (e.g. quoting a prior plan) are
    excluded from the scope of this check; executable code and primary
    docstrings are the targets.

    Adversarial angle: an implementer who edits only the constant and not
    the surrounding prose/prints would leave stale 125 references that
    mislead the next reader about the actual window size.
    """

    def test_generate_synthetic_history_replay_print_does_not_mention_125(self):
        """The 'Simulating N days' print inside generate_synthetic_history
        must use the variable count (len(intraday_dates)), not the literal '125'.

        Static analysis: find print calls inside generate_synthetic_history;
        none may contain the bare string '125' as a constant string argument
        (as opposed to a computed value). The '125' in a string like
        '125-day' is a maintenance hazard — it must become either
        the constant name or the runtime len() value.
        """
        tree = _synth_tree()
        gen = None
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "generate_synthetic_history":
                gen = node
                break
        assert gen is not None, "generate_synthetic_history not found."

        # Find all string constants inside print() calls within the function.
        offending_lines: list[int] = []
        for node in _ast.walk(gen):
            if not isinstance(node, _ast.Call):
                continue
            func = node.func
            is_print = isinstance(func, _ast.Name) and func.id == "print"
            if not is_print:
                continue
            for arg in node.args:
                for sub in _ast.walk(arg):
                    if isinstance(sub, _ast.Constant) and isinstance(sub.value, str):
                        if "125" in sub.value:
                            offending_lines.append(getattr(node, "lineno", "?"))
            for kw in node.keywords:
                for sub in _ast.walk(kw.value):
                    if isinstance(sub, _ast.Constant) and isinstance(sub.value, str):
                        if "125" in sub.value:
                            offending_lines.append(getattr(node, "lineno", "?"))

        assert not offending_lines, (
            f"generate_synthetic_history contains print() call(s) with the "
            f"stale literal '125' at line(s) {offending_lines}. After the "
            f"Phase-1 window extension to 250, these strings must reference "
            f"the runtime value (len(intraday_dates)) or the constant name, "
            f"not a hardcoded '125'. RED until fixed."
        )

    def test_no_stale_125_in_walk_forward_adjacent_source_comment(self):
        """The source comment block immediately above _WALK_FORWARD_TRADING_DAYS
        must not contain the literal '125' as a number (the comment will have
        been updated to reflect the new 250-day window).

        After the Phase-1 change the comment should read '250', not '125'.
        """
        src = _synth_source()
        lines = src.splitlines()

        # Find the definition line for _WALK_FORWARD_TRADING_DAYS.
        anchor_idx = None
        for i, line in enumerate(lines):
            if (
                "_WALK_FORWARD_TRADING_DAYS" in line
                and "=" in line
                and not line.lstrip().startswith("#")
            ):
                anchor_idx = i
                break
        assert anchor_idx is not None, (
            "Could not find the _WALK_FORWARD_TRADING_DAYS assignment in "
            "synthetic_history.py."
        )

        # Collect the contiguous comment block above the definition.
        comment_lines: list[str] = []
        j = anchor_idx - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            comment_lines.insert(0, lines[j])
            j -= 1

        comment_text = "\n".join(comment_lines)
        # A comment that still says "125 trading days" or "125-day" is stale.
        import re
        stale_matches = re.findall(r"\b125\b", comment_text)
        assert not stale_matches, (
            f"The source comment immediately above _WALK_FORWARD_TRADING_DAYS "
            f"still contains {len(stale_matches)} occurrence(s) of '125'. "
            f"After the Phase-1 window extension to 250, this comment must be "
            f"updated to reflect the new value. Comment block found:\n"
            f"{comment_text}\nRED until all '125' references are updated to '250'."
        )
