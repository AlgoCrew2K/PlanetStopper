# History Tab — Numbers Trust Audit

**Auditor:** data-flow
**HEAD:** 4a8e1e719cde4202d9635d048351a2d5f4a7afac
**API endpoint:** /api/history/30
**Source:** analytics.get_history_summary() (analytics.py:770)
**Raw data:** 4 post_mortem files in window: 2026-05-14, -15, -18, -19 (34 triggers total)
**Date:** 2026-05-20

---

## Hero Stats

| Stat | UI field | API value | Raw recompute | Display formula | CORRECT? |
|------|----------|-----------|---------------|-----------------|----------|
| Total alpha | val-total-alpha | 5.48 | 5.48 (sum of saved_pct_guard_alpha across 34 triggers) | payload.total_alpha.toFixed(2) + pct | CORRECT |
| Dollars saved | val-total-saved | 65.87 | 65.87 (sum of saved_dollars across 34 triggers) | payload.total_saved.toLocaleString 2dp | CORRECT |
| Trigger count | val-trigger-count | 34 | 34 (one per trigger entry) | String(payload.trigger_count) | CORRECT |
| Win rate | val-win-rate | 41.17647... | 14 wins / 34 triggers * 100 = 41.176% | payload.win_rate.toFixed(1) + pct | CORRECT |

**Win rate scale:** API emits percentage (0-100 scale: 41.18). JS renders  directly without multiplying -- this is correct because analytics.py:825 computes . No double-multiply bug. Display shows 41.2% which matches.

**Win definition:** analytics.py:811 -- . A win is any trigger with positive saved_pct_guard_alpha. The count (14 wins / 34 triggers) is correct from raw files.

---

## Daily Alpha Strip

| Date | API daily_alpha | Recomputed | Bar direction | CORRECT? |
|------|----------------|-----------|---------------|----------|
| 2026-05-14 | 0.03 | 0.03 (sum of 11 triggers on that day) | positive (green, above midline) | CORRECT |
| 2026-05-15 | 0.31 | 0.31 (sum of 11 triggers) | positive | CORRECT |
| 2026-05-18 | -0.24 | -0.24 (sum of 4 triggers) | negative (red, below midline) | CORRECT |
| 2026-05-19 | 5.38 | 5.38 (sum of 8 triggers) | positive | CORRECT |

**Bar math (history.js:170-180):** . maxAbs = 5.38. y = midY - barH for positive, y = midY for negative. Fill color: green if v >= 0 else red. Logic is correct -- positive bars grow upward from midline, negative bars grow downward.

---

## By-Reason Cards

| Reason | count | wins | win% (UI) | win% (raw) | total-alpha (UI) | total-alpha (raw) | dollars (UI) | dollars (raw) | avg-a/exit (UI) | avg-a/exit (raw) | CORRECT? |
|--------|-------|------|-----------|-----------|-----------------|-----------------|-------------|-------------|-----------------|-----------------|----------|
| Take-Profit | 8 | 4 | 50% | 4/8=50.0% | 4.56% | 4.56 | 5 | 5.40 | 4.56/8=0.57%a | 0.57%a | CORRECT |
| Trailing Stop | 12 | 2 | 17% | 2/12=16.67% | 0.03% | 0.03 | /usr/bin/bash | /usr/bin/bash.42 | 0.03/12=0.00%a | 0.0025%a | NOTE |
| VWAP Bleed Cut | 3 | 3 | 100% | 3/3=100.0% | 0.58% | 0.58 |  | .64 | 0.58/3=0.19%a | 0.1933%a | CORRECT |
| VWAP Breakdown | 11 | 5 | 45% | 5/11=45.45% | 0.31% | 0.31 | /usr/bin/bash | .41 | 0.31/11=0.03%a | 0.0282%a | CORRECT |

**Trailing Stop dollars note:** API has dollars=0.42 but UI renders  +  =  (rounds 0.42 to 0). The value is correct in the API; the display rounds to the nearest dollar which loses sub-dollar amounts. This is cosmetic -- the underlying number is right. history.js:196: . A display precision issue, not a math error.

**win% display:** history.js:190 computes  on the client from by_reason fields -- does not read win_rate directly from the card. This is correct.

**avg-a/exit formula:** history.js line ~203: . This divides TOTAL alpha for the reason by count, which is the mean alpha per trigger for that reason. Correct formula.

---

## Todays Exits

Today (2026-05-20) has no post_mortem file yet (market still open). todays_exits is correctly empty.

Field mapping (verified against post_mortem_2026-05-19.json):
- rec.ts <- t.get(timestamp, t.get(ts)) <- post_mortem field time_triggered e.g. 13:08
- rec.symphony_name <- _name_map.get(sym_id) or t.get(symphony_name) or sym_id -- working (D-DAT-R05 closed)
- rec.reason <- t.get(exit_reason) e.g. Trailing Stop
- rec.detail <- t.get(detail, t.get(saved_pct_guard_alpha)) -- falls back to saved_pct_guard_alpha e.g. 0.08

MINOR GAP: detail is displayed as a raw float (0.08) with no unit. The value is saved_pct_guard_alpha which is a percentage. The design spec shows it as alpha with pct sign. history.js:284: escHtml(String(rec.detail)) -- no pct appended.

---

## Window Selector

| Window | API call | trigger_count | daily_alpha bars | Verdict |
|--------|----------|--------------|-----------------|--------|
| 30d | /api/history/30 | 34 | 4 | CORRECT |
| 90d | /api/history/90 | 34 | 4 | CORRECT -- same 4 files, all < 6 days old |

Window selector is correctly wired: windowDays() maps values to integers, loadHistory() re-fetches /api/history/<N>, analytics filters by date. Identical results across 30d/90d/YTD are a data limitation (only 4 files, all < 6 days old), not a wiring bug.

---

## Summary

| Area | Verdict | Notes |
|------|---------|-------|
| total_alpha | CORRECT | 5.48 verified against raw sum |
| total_saved (dollars_saved) | CORRECT | 65.87 verified against raw sum |
| trigger_count | CORRECT | 34 exact trigger count in window |
| win_rate | CORRECT | 41.2% shown; 14/34*100=41.18 from raw; API pct-scale; JS renders direct without extra multiply |
| avg_guard_alpha | CORRECT | total_alpha/trigger_count = 0.161 |
| daily_alpha bar values | CORRECT | All 4 dates verified; pos/neg direction correct |
| by_reason counts/wins | CORRECT | All 4 reasons match raw file counts |
| by_reason win% | CORRECT | Client-computed from wins/count*100 |
| by_reason avg-alpha/exit | CORRECT | total_alpha_for_reason / count |
| by_reason dollars display | MINOR GAP | toFixed(0) rounds 0.42 to /usr/bin/bash for Trailing Stop; value is correct in API |
| todays_exits field mapping | CORRECT | All fields verified; empty today as expected |
| todays_exits detail unit | MINOR GAP | detail rendered as raw float (0.08) without pct sign; ambiguous to user |
| window selector wiring | CORRECT | JS re-fetches with correct param; same results are a data-gap not a bug |

**All computations are mathematically correct. Two cosmetic display gaps: sub-dollar rounding on by_reason cards, missing pct unit on todays_exits detail column.**
