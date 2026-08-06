"""RED — BL-7 (audit #118 D6, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
stale comment above app.py's _ADVISOR_ROLES misdescribes the current
run_divergence_explainer contract.

THE BUG: the comment (currently app.py:7463-7465, shifted from the plan's
:7406-7410 citation) claims "[DIVERGENCE_EXPLAINER] is permanently rejected
but still writes one per autotune run" — false since AC-14
(advisors/divergence_explainer.py:150-181's run_divergence_explainer is
explicit: with SECOND_WINDOW_CVAR_ENABLED off, it writes NOTHING and returns
None).

THE FIX (AC-7): correct the comment to describe the CURRENT contract
accurately, and clarify the filter immediately below exists as legacy-row
defense for the 22 pre-AC-14 NOT_APPLICABLE rows still in the DB, not as an
ongoing per-run write it needs to suppress.

AC-8: comment-only — zero functional change to the filter logic itself. No
behavioral test required beyond the existing suite staying green; this file
adds the machine-enforced guard against future re-staling the plan's Testing
Strategy calls out as the team's discretion (feedback_names_describe_
behavior_not_change_history-adjacent: a stale claim about what the code does
is worse than no comment at all).
"""

from __future__ import annotations

import pathlib

_APP_PY = pathlib.Path(__file__).parent.parent.parent / "app.py"

_STALE_CLAIM = "permanently rejected but still writes one per autotune run"


def test_stale_still_writes_one_per_run_claim_is_gone():
    """RED today: the false claim is still present verbatim in app.py. GREEN
    once the comment is corrected to describe run_divergence_explainer's
    actual write-nothing-when-disabled contract."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert _STALE_CLAIM not in src, (
        f"app.py still contains the stale claim {_STALE_CLAIM!r} — "
        "advisors/divergence_explainer.py's run_divergence_explainer writes "
        "NOTHING (returns None) when SECOND_WINDOW_CVAR_ENABLED is off; the "
        "comment must describe that contract, not the old always-writes-one "
        "behavior AC-14 removed"
    )


def test_divergence_explainer_comment_still_mentions_the_producer_by_name():
    """Non-vacuity + intent guard: the correction must still explain WHY
    DIVERGENCE_EXPLAINER is excluded from _ADVISOR_ROLES (a bare deletion of
    the stale sentence with no replacement would satisfy the test above but
    leave a future reader with no context at all)."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "DIVERGENCE_EXPLAINER" in src, (
        "app.py must still reference DIVERGENCE_EXPLAINER by name near its "
        "_ADVISOR_ROLES exclusion — the fix corrects the stale claim, it "
        "does not delete the explanatory comment entirely"
    )
