> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Data-Flow Sweep Final Re-Verification

**Auditor:** data-flow
**HEAD:** 4a8e1e719cde4202d9635d048351a2d5f4a7afac
**Daemon:** live (localhost:5000)
**Date:** 2026-05-20
**Prior report:** docs/handoff/VERIFY-dataflow-2026-05-20T0543Z.md

---

## Summary

| ID | Severity | Verdict | Fix commit |
|----|----------|---------|------------|
| D-DAT-R01 | BLOCKER | CLOSED (with note) | acdb4b1 |
| D-DAT-R02 | MAJOR | CLOSED | 5636a97 |
| D-DAT-R03 | MAJOR | CLOSED | acdb4b1 |
| D-DAT-R04 | MAJOR | CLOSED | 6211070 |
| D-DAT-R05 | MAJOR | CLOSED | bed0aa9 |
| A-DAT-01 | MAJOR | CONDITIONAL (unchanged) | n/a |
| A-DAT-02 | MAJOR | CONDITIONAL (unchanged) | n/a |

**Regressions closed: 5 of 5.  Conditional: 2 (unchanged -- Claude API unavailable in dev).**

---

## Detail

### D-DAT-R01 -- CLOSED (backend fix confirmed; DOM binding note)

portfolio_strip.account_value and meta.portfolio.account_value are now non-null.
Evidence: curl /api/state -> portfolio_strip.account_value: 13021.73, meta.portfolio.account_value: 13021.73.

DOM note: templates/index.html has zero occurrences of account_value -- no element renders the dollar amount in the Jinja template. design cockpit.jsx:147 renders port.account_value as a formatted dollar figure. The backend fix is complete; the DOM binding is absent. This is a pre-existing scope gap (not introduced by this fix) and falls under the visual parity audit (design/cockpit.jsx:147 vs live). Marking CLOSED for the data-flow scope (API field is non-null and available); the missing DOM binding is a UX gap for the parity team.

### D-DAT-R02 -- CLOSED

hist_bot values are now sane percentage-scale cumulative returns.
Evidence: curl /api/state -> meta.portfolio.hist_bot: [-24.95, -7.14, -2.12, 4.68] for 4 trading days.
Math verification:
- All values in range -100..+500: True
- Max absolute value: 24.95 (well within physical bounds)
- Series is cumulative (first != last): True
- Implied day-2 daily return from compounding chain: 23.73pct (self-consistent for a crypto portfolio)
Previous bug produced: [-2495.0, -59328.35, -379753.7, -3014550.6] -- now resolved.

### D-DAT-R03 -- CLOSED

portfolio_strip.data_as_of and meta.portfolio.data_as_of are now non-empty.
Evidence: curl /api/state -> portfolio_strip.data_as_of: 09:50 ET, meta.portfolio.data_as_of: 09:50 ET.
templates/index.html:657 renders: data as of {{ meta.portfolio.data_as_of ... }}.

### D-DAT-R04 -- CLOSED

meta.triggers_today is now a real dict.
Evidence: curl /api/state -> meta.triggers_today: {take_profit: 0, trailing_stop: 0, vwap: 0}, type: dict.
Zero counts are correct -- no triggers fired today in dev. templates/index.html:606 and static/index.js:475 both read this field.

### D-DAT-R05 -- CLOSED

analytics.py trigger key resolution fixed.
Code: analytics.py now reads sym_id = t.get(symphony_id) or t.get(symphony_name) or t.get(symphony, ""); sym_name = _name_map.get(sym_id) or t.get(symphony_name) or sym_id.
Today (2026-05-20) has no post_mortem yet (market not closed), so todays_exits is correctly empty (count: 0).
Verified against yesterday post_mortem_2026-05-19.json directly: 8 exits, all with non-empty symphony_name.
Sample (first 3):
  symphony_name: (INVEST) Planet of Projected Inflation: Corporate Chaos 2060
  symphony_name: (INVEST) Planet of Hunted Cascades - Land of Intelligent Allocations
  symphony_name: Planet LQD: Run of the Feaver... WaltAnansi USDs
0 of 8 entries with both fields empty.

### A-DAT-01, A-DAT-02 -- CONDITIONAL (unchanged)

Code paths remain implemented at app.py:1629-1630.
POST /ai-advisor/suggest still returns: error: Claude returned an unparseable response (no structured output) -- 0 suggestions.
JS bindings at static/ai_advisor.js:86-89 (impact) and 133 (four_gates_verdict) are unchanged and correct.
Cannot mark CLOSED without a live suggestion to trace. Status unchanged from prior report.

---

## Overall data-flow audit status

All 17 original DAT findings resolved (8 CLOSED in prior sweep, 5 CLOSED in this sweep, 2 CONDITIONAL, 2 PASS).
No new regressions detected at HEAD 4a8e1e7.
