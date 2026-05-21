"""
RED tests for AC-8 (VWAP System A provenance) and AC-9 (open-window grace
timezone handling) — the two LOW-severity exit-math audit items.

AC-8 (VWAP provenance, audit MEDIUM):
  compute_vwap_breakdown_update's System A gate is
      if safe_hwm >= vwap_cross_hwm_pct and current_return < safe_hwm
  The audit found no literature provenance for why the profit-protection
  break should gate on safe_hwm >= vwap_cross_hwm_pct. The decision is to
  DOCUMENT System A in-code as a tuned practitioner heuristic with no formal
  literature provenance. This test asserts the docstring/inline comment
  carries that explicit heuristic-without-provenance language.

AC-9 (open-window grace tz, audit LOW):
  is_in_open_window_grace strips tzinfo and compares naively — correct only
  if current_et is genuinely ET; a UTC caller silently shifts the grace
  window 4-5 hours. Also has a dead local `exec_start`. The fix: require /
  assert a tz-aware ET datetime (or do tz-aware arithmetic) and delete the
  dead local.

These are documentation / robustness fixes; the tests assert the contract
(comment presence, tz-awareness behavior, dead-code removal), never a
producer-computed value.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import math_engine


try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.12
    _ET = timezone(timedelta(hours=-4))

_UTC = timezone.utc


# ===========================================================================
# AC-8 — VWAP System A provenance comment
# ===========================================================================


def test_vwap_system_a_documented_as_heuristic_without_provenance() -> None:
    """
    AC-8: the VWAP System A gate (safe_hwm >= vwap_cross_hwm_pct) must be
    documented in-code as a tuned practitioner heuristic with no formal
    literature provenance.

    The audit flagged that fixtures pin the inequality DIRECTIONS but nothing
    explains WHY the profit-protection break gates on
    safe_hwm >= vwap_cross_hwm_pct, nor why that is the right gate quantity.

    This test asserts the compute_vwap_breakdown_update source (docstring +
    inline comments) contains explicit heuristic/provenance language near
    System A. It does not pin exact wording.

    RED: the current docstring describes the System A BRANCH MECHANICS but
    contains no provenance statement.
    """
    source = inspect.getsource(math_engine.compute_vwap_breakdown_update).lower()
    provenance_markers = (
        "heuristic",
        "no formal",
        "no literature",
        "no provenance",
        "practitioner",
        "tuned",
        "empirical",
    )
    assert any(m in source for m in provenance_markers), (
        "compute_vwap_breakdown_update has no provenance statement for the "
        "System A gate. AC-8 requires an in-code note that System A "
        "(safe_hwm >= vwap_cross_hwm_pct) is a tuned practitioner heuristic "
        "with no formal literature provenance."
    )


def test_vwap_system_a_provenance_note_mentions_the_gate_quantity() -> None:
    """
    AC-8: the provenance note must specifically concern the System A gate —
    a generic 'this is heuristic' comment elsewhere in the function does not
    satisfy the AC. Assert the source mentions the gate quantity
    (vwap_cross_hwm_pct or safe_hwm) in proximity to heuristic language.

    Implemented as: at least one source line contains both a provenance
    marker AND a gate-quantity reference, OR a provenance marker appears
    within 3 lines of a vwap_cross_hwm_pct reference.
    """
    src_lines = inspect.getsource(
        math_engine.compute_vwap_breakdown_update
    ).splitlines()
    provenance_markers = (
        "heuristic",
        "no formal",
        "no literature",
        "no provenance",
        "practitioner",
    )

    def is_provenance(line: str) -> bool:
        low = line.lower()
        return any(m in low for m in provenance_markers)

    def is_gate_ref(line: str) -> bool:
        low = line.lower()
        return "vwap_cross_hwm_pct" in low or "system a" in low

    near = False
    for i, line in enumerate(src_lines):
        if is_provenance(line):
            window = src_lines[max(0, i - 3): i + 4]
            if any(is_gate_ref(w) for w in window):
                near = True
                break
    assert near, (
        "The AC-8 provenance note is not anchored to the System A gate. The "
        "heuristic-without-provenance comment must sit next to the "
        "safe_hwm >= vwap_cross_hwm_pct gate (or name System A) so a future "
        "reader knows WHICH gate lacks literature support."
    )


# ===========================================================================
# AC-9 — is_in_open_window_grace timezone handling
# ===========================================================================


def test_grace_window_rejects_or_correctly_handles_naive_datetime() -> None:
    """
    AC-9: is_in_open_window_grace must require/assert a tz-aware ET datetime
    (or perform genuinely tz-aware arithmetic). A naive datetime has no
    timezone — accepting it silently is the bug the audit flagged.

    GREEN-acceptable behaviors (either satisfies AC-9):
      (a) raise an explicit error (ValueError/TypeError/AssertionError) when
          passed a naive datetime, OR
      (b) the function only ever does tz-aware arithmetic such that a naive
          input cannot misbehave.

    This test passes a NAIVE datetime; on a correct GREEN impl it must
    raise. (If the implementer chooses path (b) instead, they must update
    this test to reflect the tz-aware contract — flagged for the GREEN
    handoff.)

    RED: the current impl calls current_et.replace(tzinfo=None) and compares
    naively, silently accepting a naive datetime.
    """
    naive = datetime(2026, 5, 14, 10, 35, 0)  # no tzinfo
    with pytest.raises((ValueError, TypeError, AssertionError)):
        math_engine.is_in_open_window_grace(naive, "10:30", 15)


def test_grace_window_utc_caller_cannot_shift_the_window() -> None:
    """
    AC-9: the core bug — a UTC caller silently shifts the grace window 4-5h.

    Construct the SAME physical instant two ways: as an ET datetime and as
    the equivalent UTC datetime. is_in_open_window_grace must return the
    SAME answer for both, because they are the same instant — the grace
    window is defined in ET wall-clock and a correct tz-aware implementation
    converts before comparing.

    Scenario: 10:35 ET on a market day = 14:35 UTC (EDT, UTC-4). With
    EXECUTION_START_TIME 10:30 ET and a 15-min grace, both representations
    are inside the window -> both must return True.

    RED: the pre-fix impl strips tzinfo and reads the raw hour, so the UTC
    representation (hour 14) is compared against the ET start (hour 10) and
    returns False — a different answer for the same instant.
    """
    et_instant = datetime(2026, 5, 14, 10, 35, 0, tzinfo=_ET)
    utc_instant = et_instant.astimezone(_UTC)
    # Sanity: they are the same instant.
    assert et_instant == utc_instant, "test setup error: instants differ"

    result_et = math_engine.is_in_open_window_grace(et_instant, "10:30", 15)
    try:
        result_utc = math_engine.is_in_open_window_grace(utc_instant, "10:30", 15)
    except (ValueError, TypeError, AssertionError):
        # If the impl rejects non-ET tz-aware datetimes outright, that is an
        # acceptable AC-9 outcome (it refuses to silently mis-handle a
        # non-ET caller). Re-derive: it must NOT silently return a wrong
        # answer. Treat a clean rejection as a pass for this test.
        return

    assert result_et == result_utc, (
        f"TZ-SHIFT BUG: same physical instant, different grace answers. "
        f"ET representation -> {result_et}, UTC representation -> "
        f"{result_utc}. is_in_open_window_grace must interpret the instant "
        f"timezone-aware so a UTC caller cannot shift the grace window."
    )
    # And it must be the correct answer: 10:35 ET is inside [10:30, 10:45).
    assert result_et is True, (
        f"10:35 ET is inside the [10:30, 10:30+15min) grace window; "
        f"expected True, got {result_et}."
    )


def test_grace_window_dead_local_exec_start_removed() -> None:
    """
    AC-9: the dead local `exec_start` (the unused datetime.time at the top of
    is_in_open_window_grace) must be removed.

    The audit named this: the function builds `exec_start = _dt.time(h, m)`
    but never uses it — only exec_start_dt / grace_end_dt are used. Dead code.

    Asserts via AST: no Assign target named `exec_start` survives inside the
    function body.

    RED: the current source has `exec_start = _dt.time(h, m)` at line ~548.
    """
    source = pathlib.Path(math_engine.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    target_fn: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "is_in_open_window_grace"
        ):
            target_fn = node
            break
    assert target_fn is not None, (
        "is_in_open_window_grace not found in math_engine.py"
    )

    dead_local_assigns: list[int] = []
    for sub in ast.walk(target_fn):
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "exec_start":
                    dead_local_assigns.append(sub.lineno)
        if isinstance(sub, ast.AnnAssign):
            tgt = sub.target
            if isinstance(tgt, ast.Name) and tgt.id == "exec_start":
                dead_local_assigns.append(sub.lineno)

    assert not dead_local_assigns, (
        f"Dead local `exec_start` still assigned in is_in_open_window_grace "
        f"at line(s) {dead_local_assigns}. AC-9 requires the unused local be "
        f"deleted — only exec_start_dt / grace_end_dt are used."
    )


def test_grace_window_is_pure_and_deterministic() -> None:
    """
    AC-9 regression guard: is_in_open_window_grace must remain a pure
    function — same tz-aware ET input -> same result, no side effects. The
    tz fix must not introduce hidden state or a now()-dependence.
    """
    et_instant = datetime(2026, 5, 14, 10, 40, 0, tzinfo=_ET)
    r1 = math_engine.is_in_open_window_grace(et_instant, "10:30", 15)
    r2 = math_engine.is_in_open_window_grace(et_instant, "10:30", 15)
    assert r1 == r2, (
        f"is_in_open_window_grace is non-deterministic: {r1} vs {r2} for "
        f"identical inputs. It must stay pure after the tz fix."
    )
    assert r1 is True, (
        f"10:40 ET is inside [10:30, 10:45) grace; expected True, got {r1}."
    )
