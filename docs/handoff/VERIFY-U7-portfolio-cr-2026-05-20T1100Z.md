> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# U7-01 — Portfolio-Level CR Diagnosis

**Auditor:** data-flow
**HEAD:** 913c0194512e4f62c13a44f4f7920fc3e8f6f35b
**Date:** 2026-05-20

---

## Displayed vs Correct Values

| Metric | Displayed | Correct value | Match? |
|--------|-----------|--------------|--------|
| CR Bot (dry_run) | -0.96% | -0.96% | CORRECT |
| CR Held (if_held) | +67.00% | +67.00% | CORRECT (see note) |
| Today Bot | -1.77% | -1.77% | CORRECT |
| Today Held | -1.81% | -1.81% | CORRECT |
| MDD Bot (dry_run) | +2.39% | +2.39% | CORRECT |
| MDD Held (if_held) | +0.19% | +19.17% | WRONG -- unit error |

---

## Finding 1: Portfolio CR if_held = 67% is mathematically correct but inflated by one outlier symphony

The value-weighted CR computation is correct. The 67% figure is driven by one symphony:
- Symphony n2ooAZTvBRN6ZzpMmWmU ("We do a Little Trolling..." crypto, since Oct 2022)
  current_value=1629.84, simple_return=0.0, net_deposits=0.0, time_weighted_return=3.1847
- When simple_return==0 AND net_deposits==0, analytics.py:544-545 falls back to TWR*100 = 318.47%
- This one entry has weight 1629.84 * 318.47 = 519,268 in the weighted sum
- The remaining 10 symphonies average ~29% CR (range -7.6% to +79.8%)
- Portfolio value-weighted avg = 67.00% -- recomputed manually and matches API exactly

**Is the 318.47% figure correct?**
Yes. net_deposits=0.0 for this symphony means Composer has no deposit-tracking for it
(likely pre-dates the deposit-tracking feature). simple_return is therefore undefined
(would require net_deposits > 0 in the denominator). The TWR fallback is the correct
behavior per analytics.py:528-533 docstring. The crypto symphony started at inception
in Oct 2022 and has grown 3.18x, so a 318% TWR is plausible.

**Why does the user find it suspicious?**
A portfolio CR of 67% when most symphonies show 9-80% individual CRs is counter-intuitive
because the 318% outlier is pulling the value-weighted average up sharply. This is not a
bug -- it is accurate data correctly aggregated. The display could add context (e.g. the
outlier symphony label) but the number itself is correct.

---

## Finding 2: MDD if_held unit error -- WRONG (19.17% shown as 0.19%)

**Root cause:** analytics.py:584

Composer stores max_drawdown as a FRACTION (e.g. 0.1478 = 14.78%). analytics.py does NOT
multiply by 100. The value-weighted portfolio MDD if_held = 0.1917 (fraction) is stored
directly in portfolio_strip.max_drawdown.if_held.

index.js:444 then renders it as  which appends  directly, so 0.1917
displays as +0.19% instead of the correct +19.17%.

**Verification:**
All 11 per-symphony max_drawdown values: [0.147, 0.234, 0.234, 0.150, 0.147, 0.323,
0.148, 0.232, 0.255, 0.125, 0.066] -- clearly fractions (0.0 to 1.0 range), not pct.
Value-weighted average = 0.1917. Multiplied by 100 = 19.17%.
API emits 0.1917. Display shows 0.19%. Correct display should be 19.17%.

**Same issue applies to MDD dry_run = 2.39 -- but dry_run is computed from shadow
trajectory as a percentage already (analytics.py:598: ), so dry_run
is correct as-is. Only if_held has the fraction-vs-percentage mismatch.

**Fix recipe (backend, analytics.py:585):**

Change:
    if_held = float(sym_dict["max_drawdown"])
To:
    if_held = float(sym_dict["max_drawdown"]) * 100.0

This is the ONLY change required. dry_run already returns a percentage (from shadow
trajectory arithmetic) and must NOT be changed. index.js renders both fields with
fmtPct() which appends a percent sign directly -- no JS change needed.

---

## Summary

| Finding | Verdict | File:line | Fix needed |
|---------|---------|-----------|------------|
| CR dry_run = -0.96% | CORRECT | analytics.py:559 (shadow trajectory) | None |
| CR if_held = +67% | CORRECT (outlier inflated) | analytics.py:544-547 (TWR fallback) | None |
| Today Bot = -1.77% | CORRECT | app.py:422, analytics.py:404 | None |
| Today Held = -1.81% | CORRECT | app.py:422, analytics.py:404 | None |
| MDD dry_run = +2.39% | CORRECT | analytics.py:598-608 (shadow trajectory) | None |
| MDD if_held = +0.19% | WRONG: should be +19.17% | analytics.py:585 | * 100.0 |

One backend fix required. One line. No JS changes needed.
