"""Retirement Checklist -- Phase 2 Cycle 2b (advisory, deterministic, no LLM).

Builds a fixed, deterministic wind-down checklist for a retirement
recommendation the operator has approved: the candidate symphony's id (+
display name when resolvable), its current holdings, and a fixed set of
manual steps the operator performs by hand in Composer. This module never
calls a language model of any kind -- it contains no LLM client, no
generation call, and no reference to this codebase's LLM client seam. It
is the one entirely deterministic module in this feature (operator ruling,
Gate-2b): the checklist is a template, not a generated artifact.

This module has NO trade, order, liquidation, deploy, or live-execution
primitive of any kind -- it never moves money, never writes settings, and
never writes to any table. It reaches no exec/trade-write module anywhere
in this codebase. Off the 1-minute execution path; never raises (D-1
honest-degradation contract, matching the sibling advisors modules).

Honest off-hours degrade: when the candidate's live holdings are
unavailable (off-hours / weekend / flat market -- the documented
`logic_holdings` runtime-field caveat), the checklist reports zero
holdings and an explicit unavailability note -- it never fabricates a
ticker.

No magic numbers -- the fixed step text and unavailability note are named
module-level constants.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Fixed advisory content (named constants -- no magic strings inline)
# ---------------------------------------------------------------------------

# AC-6: "the fixed manual steps (advisory prose the operator performs in
# Composer)". Deliberately generic -- these steps describe manual actions
# the OPERATOR takes in Composer's own UI; this module never performs any
# of them itself.
_CHECKLIST_STEPS: tuple[str, ...] = (
    "Open the candidate symphony directly in Composer.",
    "Cross-check the holdings listed below against the live position view in Composer.",
    "Manually wind down (sell or liquidate) each listed position within "
    "Composer -- this checklist does not execute any trade itself.",
    "Confirm the symphony's cash balance reflects the completed wind-down.",
    "Pause or archive the symphony in Composer once the wind-down is confirmed.",
)

# AC-6: the honest off-hours degrade note -- populated only when the
# candidate's current holdings could not be resolved from the live state.
_HOLDINGS_UNAVAILABLE_NOTE = (
    "current holdings unavailable (off-hours) -- view live positions in Composer"
)


def build_checklist(recommendation: dict, bot_state: dict) -> dict:
    """Return a deterministic, advisory wind-down checklist for a candidate.

    Args:
        recommendation: a retirement recommendation dict carrying at least
            `candidate_id` (a Cycle-2a raw_response shape, but any dict
            with that key works).
        bot_state: the live state dict (as returned by the state-loading
            accessor elsewhere in this codebase) used to resolve the
            candidate's display name and current holdings tickers.

    Returns:
        A dict with keys `candidate_id`, `candidate_name`, `holdings`,
        `holdings_available`, `steps`, `unavailable_note`. Never raises,
        regardless of malformed/missing/None input.
    """
    rec = recommendation if isinstance(recommendation, dict) else {}
    candidate_id = rec.get("candidate_id")

    state = bot_state if isinstance(bot_state, dict) else {}
    entry = state.get(candidate_id) if candidate_id is not None else None
    candidate_name = entry.get("name") if isinstance(entry, dict) else None

    logic_holdings = entry.get("logic_holdings") if isinstance(entry, dict) else None
    if isinstance(logic_holdings, dict) and logic_holdings:
        holdings = sorted(logic_holdings.keys())
        holdings_available = True
        unavailable_note = None
    else:
        holdings = []
        holdings_available = False
        unavailable_note = _HOLDINGS_UNAVAILABLE_NOTE

    return {
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "holdings": holdings,
        "holdings_available": holdings_available,
        "steps": list(_CHECKLIST_STEPS),
        "unavailable_note": unavailable_note,
    }
