# Data-Flow Sweep Re-Verification

**Auditor:** data-flow  
**HEAD:** f3410528852635dbeba871cf907ebd98f9dcdb0e  
**Daemon:** live (localhost:5000)  
**Date:** 2026-05-20  

---

## Summary

| Finding ID | Prior | Verdict | Notes |
|------------|-------|---------|-------|
| D-DAT-01 | BLOCKER | CLOSED | portfolio_strip.cumulative_return now non-zero |
| D-DAT-02 | BLOCKER | OPEN->D-DAT-R01 | account_value is None in portfolio_strip and meta |
| D-DAT-03 | MAJOR | OPEN->D-DAT-R02 | hist_bot compounding formula unit error: f_ret is pct not fraction |
| D-DAT-04 | MAJOR | OPEN->D-DAT-R03 | data_as_of empty; absent from portfolio_strip |
| D-DAT-05 | MAJOR | OPEN->D-DAT-R04 | triggers_today absent from app.py _build_meta |
| D-DAT-06 | MAJOR | CLOSED | hero-tracked and hero-armed IDs now in index.html |
| D-DAT-07 | MINOR | CLOSED | guard_alpha headline reads real portfolio_strip on poll |
| P-DAT-01 | PASS | PASS | win_rate fraction confirmed |
| P-DAT-02 | MINOR | CLOSED | /api/performance echoes window_days: 60 |
| P-DAT-03 | PASS | PASS | fmt() isFinite guard unchanged |
| A-DAT-01 | MAJOR | CONDITIONAL | Code path at app.py:1629 exists; Claude unparseable in dev -- 0 suggestions |
| A-DAT-02 | MAJOR | CONDITIONAL | Code path at app.py:1630 exists; same Claude unavailability |
| A-DAT-03 | MAJOR | CLOSED | autotune-runs baseline_decision emits short enum: fallback |
| A-DAT-04 | MINOR | CLOSED | autotune-runs frozen_eval_verdict emits string: failed |
| H-DAT-01 | MAJOR | OPEN->D-DAT-R05 | todays_exits symphony_id and symphony_name both empty string |
| H-DAT-02 | MINOR | CLOSED | /api/history/30 todays_exits array present (8 entries) |
| H-DAT-03 | MINOR | CLOSED | /api/history/30 echoes window_days: 30 |

**Closed: 8  |  Conditional (Claude unavailable in dev): 2  |  Still open: 5**

---

## Detail

### D-DAT-01 -- CLOSED

 portfolio_strip.cumulative_return.dry_run = 0.9062, if_held = 67.745.
Evidence: curl /api/state -> portfolio_strip: {cumulative_return: {dry_run: 0.9061568591064518, if_held: 67.74513844519815}}

### D-DAT-02 -- STILL OPEN (regression D-DAT-R01) BLOCKER

meta.portfolio.account_value is None. portfolio_strip contains only {cumulative_return, max_drawdown, today_change} -- no account_value key.
account_value is computed in alpha_bot_execution.py:927 during live execution cycles but is never written into the portfolio_strip dict that _build_meta reads.
Evidence: curl /api/state -> portfolio_strip keys: [cumulative_return, max_drawdown, today_change]. meta.portfolio.account_value: null.

### D-DAT-03 -- STILL OPEN (regression D-DAT-R02) MAJOR

hist_bot compounding formula at app.py:530 is  treating _bot_daily as a decimal fraction. But analytics f_ret values are already percentage-scale (e.g. -1.85 means -1.85%).
So  = -0.85, which compounds to physically impossible values.
Evidence: live hist_bot = [-2495.0, -59328.35, -379753.7235, -3014550.56459] for 4 trading days.
Direct analytics check: first sym f_ret = -1.85 (pct-scale, not fraction).
Fix: app.py:530 change to  and same for _running_held.

### D-DAT-04 -- STILL OPEN (regression D-DAT-R03) MAJOR

meta.portfolio.data_as_of is empty string. portfolio_strip has no data_as_of key; it contains only {cumulative_return, max_drawdown, today_change}.
_build_meta at app.py:365 reads ps.get(data_as_of, "") but ps never has this field.
data_as_of is written in app.py:1019 during intraday chart builds to a DB snapshot, but get_state portfolio_strip is built from analytics and does not include it.
Evidence: curl /api/state -> meta.portfolio.data_as_of: ""

### D-DAT-05 -- STILL OPEN (regression D-DAT-R04) MAJOR

meta.triggers_today is null in API response. The string triggers_today does not appear in app.py -- there is no code path that builds or populates this field for _build_meta.
Evidence: grep triggers_today in app.py returns zero matches. Live API: meta.triggers_today: null.
Template templates/index.html:606 reads meta.triggers_today with a safe fallback to {} so chips render 0 gracefully, but no real counts are shown.

### D-DAT-06 -- CLOSED

id=hero-tracked at templates/index.html:746, id=hero-armed at templates/index.html:750.
JS poll updates in static/index.js:244-247 will now land on the correct DOM elements.

### D-DAT-07 -- CLOSED

portfolio_strip.cumulative_return.{dry_run, if_held} are non-zero. renderGuardAlpha at static/index.js:98-109 reads from data.portfolio_strip on first poll.

### P-DAT-02 -- CLOSED

curl /api/performance -> window_days: 60 present in response keys.

### A-DAT-01, A-DAT-02 -- CONDITIONAL

Code path implemented: app.py:1629 calls _compute_suggestion_gates, app.py:1630 calls _enrich_suggestion_impact.
JS bindings at static/ai_advisor.js:86-89 (impact) and 133 (four_gates_verdict) read these fields.
However: POST /ai-advisor/suggest returns error: Claude returned an unparseable response (no structured output) with 0 suggestions in dev environment.
The enrichment code is not live-exercisable. Cannot mark CLOSED from a live trace.
Marking CONDITIONAL: code path is present and wired; closes when Claude API returns parseable output.

### A-DAT-03 -- CLOSED

curl /api/autotune-runs first run: baseline_decision: fallback (short enum). JS at static/ai_advisor.js:399 reads r.baseline_decision.

### A-DAT-04 -- CLOSED

curl /api/autotune-runs first run: frozen_eval_verdict: failed (string verdict). JS at static/ai_advisor.js:401 reads r.frozen_eval_verdict.

### H-DAT-01 -- STILL OPEN (regression D-DAT-R05) MAJOR

All 8 todays_exits entries have symphony_id: "" and symphony_name: "" (empty strings).
Root cause: analytics.py:852 does sym_id = t.get(symphony_id, t.get(symphony, "")).
But post_mortem trigger entries use the field name symphony_name for the symphony identifier string, not symphony_id or symphony.
Evidence: post_mortem_2026-05-19.json trigger keys include symphony_name: "(INVEST) Planet of Projected Inflation: Corporate Chaos 2060" -- no symphony_id key present.
Fix: analytics.py:852 should read: sym_id = t.get(symphony_id) or t.get(symphony_name, t.get(symphony, ""))
Note: once sym_id is the name string, _name_map.get(sym_id, sym_id) will fall back to the name itself since _name_map keys are IDs not names. symphony_name in the output will be the raw name string from the post_mortem file.

### H-DAT-02 -- CLOSED

curl /api/history/30 -> todays_exits array with 8 entries present.

### H-DAT-03 -- CLOSED

curl /api/history/30 -> window_days: 30 present.

---

## Regression Findings

| ID | Severity | Description | Location | Fix |
|----|----------|-------------|----------|-----|
| D-DAT-R01 | BLOCKER | account_value never in portfolio_strip; meta.portfolio.account_value always None | alpha_bot_execution.py:927 (local only) | Write account_value into portfolio_strip after each execution cycle |
| D-DAT-R02 | MAJOR | hist_bot/hist_held compounding multiplies by (1+pct) instead of (1+pct/100); produces values in millions | app.py:530 | Divide f_ret and live_ret by 100.0 before compounding |
| D-DAT-R03 | MAJOR | data_as_of never in portfolio_strip; meta.portfolio.data_as_of always empty | app.py:365 + get_state() | Include data_as_of in portfolio_strip when building it |
| D-DAT-R04 | MAJOR | triggers_today absent from _build_meta and portfolio_strip; status strip chips always 0 | app.py: zero occurrences | Count today triggers in get_state() and include in portfolio_strip |
| D-DAT-R05 | MAJOR | todays_exits sym_id resolution fails; post_mortem uses symphony_name not symphony_id | analytics.py:852 | Add symphony_name as fallback key in t.get() chain |

