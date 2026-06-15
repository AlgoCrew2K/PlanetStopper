"""
Tests for AC-8 (VWAP System A provenance) and AC-9 (open-window grace tz).

AC-8 history:
  PRE-M3: the audit flagged compute_vwap_breakdown_update's System A gate
  (safe_hwm >= vwap_cross_hwm_pct) as having no literature provenance.
  The AC-8 RED required an in-code "tuned practitioner heuristic" flag so a
  future reader knows the gate's provenance was open. That precondition was
  CORRECT for the pre-M3 era.

  POST-M3 (cycle fix-m3-provenance): the M3 redrive CLOSES the provenance
  gap by re-framing the gate as the regime boundary of a two-regime
  trailing-stop system under the Leung & Zhang (2019) optimal-stopping
  formalism / Peskir (1998) maximality principle. The runtime arithmetic
  is byte-identical (provenance-only closure per literature-pass.md §2.3);
  the in-code provenance comment now CITES the derivation instead of
  flagging an open gap.

  These tests are UPDATED to assert the M3 closure: the new THEORY
  citations are present AND the old heuristic / no-formal-provenance flag
  language is absent. The AC-8 contract has inverted -- pre-M3 asserted
  the gap flag is present; post-M3 asserts the closure citation is
  present and the gap flag is gone. The M3 plan at §49 + §131 explicitly
  authorizes this rewrite ('The companion test file's AC-7 / AC-8
  assertions are updated to reference the new provenance comment').

AC-9 (open-window grace tz, audit LOW):
  is_in_open_window_grace strips tzinfo and compares naively — correct only
  if current_et is genuinely ET; a UTC caller silently shifts the grace
  window 4-5 hours. Also has a dead local `exec_start`. The fix: require /
  assert a tz-aware ET datetime (or do tz-aware arithmetic) and delete the
  dead local. UNCHANGED by M3.

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


def test_vwap_system_a_documented_with_closed_provenance() -> None:
    """
    AC-8 (post-M3): the VWAP System A gate (safe_hwm >= vwap_cross_hwm_pct)
    must carry an in-code provenance comment citing the optimal-stopping
    derivation that closes the pre-M3 gap. The M3 closure (per
    docs/research/m3-provenance/literature-pass.md §2.3) re-frames the
    scalar gate as the regime boundary of a two-regime trailing-stop
    system under Leung & Zhang (2019) / Peskir (1998).

    Inverted from pre-M3 AC-8: pre-M3 asserted the heuristic-without-
    provenance flag was PRESENT; post-M3 asserts the closure citations are
    present AND the gap flag is absent. M3 plan §49 authorizes this update.

    The function source (docstring + inline comments) must contain AT LEAST
    ONE marker from each of two groups:
      - Closure citations (at least one): 'leung', 'peskir', 'maximality',
        'regime-switch', 'optimal-stopping' / 'optimal stopping' / 'trailing
        boundary' / 'theory'.
      - Negative: NONE of the pre-M3 gap flags ('no formal literature
        provenance', 'no provenance', 'heuristic with no') must remain in
        the System A comment region.
    """
    source = inspect.getsource(math_engine.compute_vwap_breakdown_update).lower()

    closure_markers = (
        "leung",
        "peskir",
        "maximality",
        "regime-switch",
        "regime switch",
        "optimal-stopping",
        "optimal stopping",
        "trailing boundary",
        "theory",
    )
    assert any(m in source for m in closure_markers), (
        "compute_vwap_breakdown_update has no M3 closure citation. AC-8 "
        "post-M3 requires the System A gate comment to cite the optimal-"
        "stopping derivation (Leung & Zhang 2019 / Peskir 1998 maximality "
        "principle / regime-switch construction / THEORY discipline). The "
        "pre-M3 'heuristic without provenance' flag has been replaced; the "
        "comment must now cite the published derivation, not flag an open "
        "gap. Research note: docs/research/m3-provenance/literature-pass.md."
    )

    # Negative: the M3 closure must have REMOVED the pre-M3 gap-flag phrases
    # from the System A comment region. A half-done closure (new citation
    # added but old self-flag left in) is a regression.
    gap_phrases = (
        "no formal literature provenance",
        "no provenance",
        "heuristic with no",
    )
    surviving = [p for p in gap_phrases if p in source]
    assert not surviving, (
        f"compute_vwap_breakdown_update still contains pre-M3 gap-flag "
        f"language: {surviving}. The M3 closure replaces the open-gap flag "
        f"with the THEORY-derivation citation; leaving the flag behind "
        f"defeats the closure (a future reader cannot tell whether the gap "
        f"is open or closed). Remove the surviving phrase(s)."
    )


def test_vwap_system_a_provenance_note_anchored_to_the_gate_quantity() -> None:
    """
    AC-8 (post-M3): the closure citation must specifically concern the
    System A gate — a generic Leung-Zhang reference elsewhere in the
    function does not satisfy the AC. Assert the closure citation appears
    in proximity to the gate quantity (vwap_cross_hwm_pct or 'System A').

    Same anchoring contract as pre-M3 (the citation must be next to WHAT
    it documents); only the citation language has changed.

    Implemented as: at least one source line containing a closure-citation
    marker appears within a 6-line window of a line containing the gate
    quantity. The window is widened from pre-M3's 3 to 6 because the new
    citation is a multi-line block (citation + research-note path + THEORY
    declaration) and the gate-quantity reference may sit a few lines below
    the comment header.
    """
    src_lines = inspect.getsource(math_engine.compute_vwap_breakdown_update).splitlines()
    closure_markers = (
        "leung",
        "peskir",
        "maximality",
        "regime-switch",
        "regime switch",
        "optimal-stopping",
        "optimal stopping",
        "trailing boundary",
    )

    def is_closure_citation(line: str) -> bool:
        low = line.lower()
        return any(m in low for m in closure_markers)

    def is_gate_ref(line: str) -> bool:
        low = line.lower()
        return "vwap_cross_hwm_pct" in low or "system a" in low

    near = False
    for i, line in enumerate(src_lines):
        if is_closure_citation(line):
            window = src_lines[max(0, i - 6) : i + 7]
            if any(is_gate_ref(w) for w in window):
                near = True
                break
    assert near, (
        "The AC-8 closure citation is not anchored to the System A gate. "
        "The M3 closure (Leung & Zhang 2019 / Peskir 1998 / maximality "
        "principle / regime-switch construction) must sit next to the "
        "safe_hwm >= vwap_cross_hwm_pct gate (or name 'System A') so a "
        "future reader knows WHICH gate the closure documents. Research "
        "note: docs/research/m3-provenance/literature-pass.md §2.3."
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
        f"10:35 ET is inside the [10:30, 10:30+15min) grace window; expected True, got {result_et}."
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
        if isinstance(node, ast.FunctionDef) and node.name == "is_in_open_window_grace":
            target_fn = node
            break
    assert target_fn is not None, "is_in_open_window_grace not found in math_engine.py"

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
    assert r1 is True, f"10:40 ET is inside [10:30, 10:45) grace; expected True, got {r1}."
