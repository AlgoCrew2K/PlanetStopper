"""RED — BL-11 AC-19 (audit #118 T6), team-lead-ruled DEVIATION from the
plan's literal wording.

AC-19's letter says: "if any existing operator-facing surface displays the
RAW oos_alpha sum without its avg_oos_alpha companion, surface avg_oos_alpha
alongside it there too." A real surface DOES exist —
static/ai_advisor.js:119-129 renders ``assessment.oos_alpha`` (sourced from
ai_advisor.py:1375's ``autotune_run.get("oos_alpha")``, the same multi-day-
SUM autotune_runs column BL-11 is about) as "OOS alpha: <code>...</code>",
with no avg companion.

A genuine companion-value fix is infeasible in this bundle's scope: avg_
oos_alpha is never persisted (autotuner.py:3043 computes it as a purely
local print-statement variable inside run_simulation, with no autotune_runs
column and no save_autotune_run parameter) — surfacing a real companion
number would require a new schema column, outside BL-11's stated Architecture
scope (autotuner.py comment + database.py docstring only) and outside this
hygiene bundle's "no schema migration" spirit.

TEAM-LEAD RULING (documented here per the escalation-response instruction —
also recorded in DECISIONS.md by bl5doc): satisfy AC-19's INTENT via
disclosure-by-labeling instead of a companion value — relabel the JS string
so the operator reading "OOS alpha" is told, in the label itself, that the
number is a cumulative sum across all triggered replay days, not a per-day
or annualized figure. Zero schema/route change; one JS string constant.
"""

from __future__ import annotations

import pathlib

_AI_ADVISOR_JS = pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js"

_STALE_UNQUALIFIED_LABEL = "'OOS alpha: <code>'"
_SUM_DISCLOSURE_WORDS = ("cumulative", "sum")


def test_oos_alpha_label_no_longer_unqualified():
    """RED today: the exact unqualified label literal is still present."""
    src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
    assert _STALE_UNQUALIFIED_LABEL not in src, (
        f"static/ai_advisor.js still renders the unqualified label "
        f"{_STALE_UNQUALIFIED_LABEL!r} for assessment.oos_alpha — AC-19 "
        "(team-lead-ruled disclosure-by-labeling) requires the label itself "
        "to disclose the cumulative-sum-across-triggered-days convention, "
        "since no avg_oos_alpha companion value is persisted anywhere to "
        "surface alongside it"
    )


def test_oos_alpha_label_discloses_sum_convention():
    """GREEN-target proof: the relabeled string must actually say something —
    not just differ from the stale literal (which a trivial synonym swap with
    no real disclosure would also satisfy)."""
    src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
    idx = src.find("OOS alpha")
    assert idx != -1, "fixture integrity: no 'OOS alpha' label found in static/ai_advisor.js"
    window = src[max(0, idx - 50) : idx + 150].lower()
    assert any(w in window for w in _SUM_DISCLOSURE_WORDS), (
        f"the 'OOS alpha' label region must disclose the cumulative-sum "
        f"convention (keywords: {_SUM_DISCLOSURE_WORDS}) — got window:\n{window}"
    )
