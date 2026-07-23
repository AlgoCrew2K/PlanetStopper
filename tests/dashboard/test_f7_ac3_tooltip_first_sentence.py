"""
RED tests -- F7 AC-3: the MC Prob tooltip truth correction.

Cycle context (feature-plans/math-f7.md + team-lead's LOCKED ruling,
2026-07-18)
---------------------------------------------------------------------------
The MC Prob column's tooltip (``templates/table_partial.html:46``) claims
the statistic is "probability this symphony beats SPY over the simulation
horizon" -- flatly wrong. f7-dash traced the real mechanism
(math_engine.py: the CONFIRM/trigger gate at ``compute_exit_confirmation``
fires on a HIGH reading confirming underperformance vs the symphony's own
regime-matched historical baseline; the separate TAKE-PROFIT gate at
``compute_tp_confirmation`` arms on a LOW reading of that SAME statistic --
two mechanisms, opposite directions, same stat -- so no single directional
clause can be truthful). Team-lead's ruling: ship the corrected FIRST
sentence only, with NO directional "low/high arms X" second sentence.

LOCKED CONTRACT (team-lead, verbatim)
--------------------------------------
Exact text required at ``templates/table_partial.html:46``:

    "Monte Carlo probability this symphony underperforms its own
    regime-matched historical baseline."

Plus: ZERO occurrences of "beats SPY" (case-insensitive) anywhere under
``templates/`` or ``static/`` (repo-wide sweep, not just the one file).
``.design-handoff/`` is explicitly scoped OUT (verified unreachable --
``app.py`` uses ``Flask(__name__)`` with no ``template_folder`` /
``static_folder`` override, confirmed independently by both f7-dash and
f7-test) and is therefore excluded from the sweep by construction (it sits
outside both directories).

RED STATUS (HEAD a904374d, no F7 code yet)
-------------------------------------------
Both tests are RED: the current tooltip text is the old "beats SPY" claim.
"""

from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_TABLE_PARTIAL = _REPO_ROOT / "templates" / "table_partial.html"

_LOCKED_TOOLTIP_TEXT = (
    "Monte Carlo probability this symphony underperforms its own "
    "regime-matched historical baseline."
)


def test_mc_prob_tooltip_matches_locked_exact_text() -> None:
    """The mc_prob column header's title attribute must contain the
    team-lead-LOCKED verbatim first sentence -- pinned exactly (a ratified
    UI-copy contract, not a producer-computed value; see quant-test-writer
    anti-hardcoding rule, which applies to computed rates/hours/dollars/
    percents, not fixed copy strings any more than it would forbid
    asserting a fixed status label like "triggered")."""
    assert _TABLE_PARTIAL.exists(), "templates/table_partial.html must exist"
    content = _TABLE_PARTIAL.read_text(encoding="utf-8")
    assert _LOCKED_TOOLTIP_TEXT in content, (
        f"AC-3 FAIL: templates/table_partial.html does not contain the "
        f"LOCKED tooltip text {_LOCKED_TOOLTIP_TEXT!r}. Team-lead ruled "
        f"first-sentence-only (no directional 'low/high arms X' clause) "
        f"after f7-dash's trace showed the same statistic gates the "
        f"trailing-stop CONFIRM (high) and take-profit ARM (low) in "
        f"opposite directions -- no single directional sentence is "
        f"truthful."
    )


def test_no_beats_spy_claim_anywhere_under_templates_or_static() -> None:
    """Repo-wide sweep (per team-lead's explicit instruction): zero
    occurrences of "beats SPY" (case-insensitive) anywhere under
    templates/ or static/. .design-handoff/ sits outside both directories
    and is excluded from this sweep by construction (verified unreachable
    by Flask -- no template_folder/static_folder override -- so it is not
    a live-serving surface)."""
    hits: list[str] = []
    for directory_name in ("templates", "static"):
        directory = _REPO_ROOT / directory_name
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "beats spy" in text.lower():
                hits.append(str(path.relative_to(_REPO_ROOT)))

    assert not hits, (
        f"AC-3 FAIL: found 'beats SPY' (case-insensitive) in the following "
        f"live-serving files: {hits!r}. Every occurrence under templates/ "
        f"or static/ must be swept and corrected -- silence on any hit is "
        f"a finding, per the plan's ADDENDUM."
    )
