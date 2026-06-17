# F5 Fundamentals Vintage Audit — Finding (REVISED)

**Auditor:** closeout-fund-audit  
**Date:** 2026-06-17  
**SHA audited:** 73dc603 (origin/main, closeout-audit worktree)  
**Scope:** Read-only. No code changes. SEC probes are keyless public HTTP GETs.

---

## Verdict: TWO CONCURRENT DEFECTS — BOTH CONFIRMED

The F5 fundamentals vintage failure has two distinct root causes with different fix scopes. Both are confirmed by live runnable evidence.

---

## Mode A — XBRL Concept Deprecation (PRIMARY for MSFT Revenues)

**Root cause:** `ai_advisor.py:354–360` (`_SEC_KEY_CONCEPTS`) hardcodes `"Revenues"` with no fallback/equivalence list. MSFT migrated away from `us-gaap:Revenues` after FY2008-09 to `SalesRevenueNet` (through ~FY2017) then `RevenueFromContractWithCustomerExcludingAssessedTax` (current). The `Revenues` tag for MSFT now contains only pre-migration entries — latest `end=2010-06-30` in EDGAR, nothing more recent. The producer faithfully returns the newest entry *within that deprecated tag*, but the tag itself is stale for MSFT.

**Clincher evidence — MSFT revenue-equivalent concept presence:**

| Concept | Present in MSFT us-gaap? | Latest end (correct selection) | n 10-K entries |
|---|---|---|---|
| `Revenues` | YES — but DEPRECATED | 2010-06-30 | 15 |
| `SalesRevenueNet` | YES | 2017-06-30 | 77 |
| `RevenueFromContractWithCustomerExcludingAssessedTax` | YES — CURRENT | **2025-06-30** | 48 |
| `RevenueFromContractWithCustomerIncludingAssessedTax` | ABSENT | — | — |
| `SalesRevenueGoodsNet` | YES | 2017-06-30 | 6 |
| `SalesRevenueServicesNet` | ABSENT | — | — |

The current MSFT revenue data is in `RevenueFromContractWithCustomerExcludingAssessedTax` (val=281,724,000,000, end=2025-06-30) — present in EDGAR, unreached by the producer because `_SEC_KEY_CONCEPTS` only queries `Revenues`.

**JPM control (Mode A does NOT apply):** JPM still files under `us-gaap:Revenues` (51 10-K entries, latest end=2025-12-31, no equivalents present). JPM staleness is purely Mode B.

**File:line:** `ai_advisor.py:354–360` (`_SEC_KEY_CONCEPTS` dict — hardcoded concept tag with no fallback)

---

## Mode B — Array-Selection Sort Order (AFFECTS ALL TICKERS, ALL CONCEPTS)

**Root cause:** `ai_advisor.py:1008–1019` sorts `entries_to_check` by `filed` (filing submission date) descending, takes `[0]`. The SEC companyfacts API bundles comparative prior-period entries inside a single 10-K accession. When multiple entries share the same `filed` date (the entire historical restatement set filed on one day), Python's stable sort preserves the SEC's original delivery order — **oldest `end` first**. `sorted(..., reverse=True)[0]` then picks the oldest comparative period entry, not the most recent.

This is a secondary defect distinct from Mode A: it affects concepts that are NOT deprecated (NetIncomeLoss, Assets, Liabilities, StockholdersEquity for MSFT; all 5 concepts for JPM).

**File:line:** `ai_advisor.py:1008–1019` (sort key `e.get("filed", "")` should be `e.get("end", "")`)

---

## Decisive Test Results — MSFT All 5 Concepts

**Mode A applies only to Revenues. Mode B applies to all 5.**

| Concept | Producer picks (end) | Correct latest end | Gap | Root cause |
|---|---|---|---|---|
| Revenues | 2007-09-30 | **2025-06-30** (via `RevenueFromContractWithCustomerExcludingAssessedTax`) | ~17–18 yr effective gap | **Mode A** (deprecated tag) + Mode B within deprecated tag |
| NetIncomeLoss | 2023-06-30 | 2025-06-30 | 2 yr | **Mode B only** (tag current, sort wrong) |
| Assets | 2024-06-30 | 2025-06-30 | 1 yr | **Mode B only** |
| Liabilities | 2024-06-30 | 2025-06-30 | 1 yr | **Mode B only** |
| StockholdersEquity | 2023-06-30 | 2025-06-30 | 2 yr | **Mode B only** |

The MSFT Revenues stale value (end=2007-09-30) observed in the live render is Mode B *within* the already-deprecated `Revenues` tag. The effective staleness is ~17–18 years because the entire `Revenues` tag is deprecated (Mode A) and the sort picks the oldest entry within that stale tag (Mode B compounding).

MSFT Assets/Liabilities (end=2024-06-30, filed=2025-07-30, form=10-K) confirming Mode B: the FY2025 10-K includes comparative FY2024 balance sheet figures; both share `filed=2025-07-30`; stable sort picks the FY2024 entry first.

---

## JPM Control — All Concepts (Mode B, No Mode A)

| Concept | Producer picks (end) | Correct latest end | Gap | Root cause |
|---|---|---|---|---|
| Revenues | 2023-12-31 | 2025-12-31 | 2 yr | Mode B |
| NetIncomeLoss | 2023-12-31 | 2025-12-31 | 2 yr | Mode B |
| Assets | 2023-12-31 | 2025-12-31 | 2 yr | Mode B |
| Liabilities | 2024-12-31 | 2025-12-31 | 1 yr | Mode B |
| StockholdersEquity | 2023-12-31 | 2025-12-31 | 2 yr | Mode B |

JPM has no revenue-equivalent concepts other than `Revenues` — Mode A does not apply.

---

## Summary: Two Fixes Needed (follow-on, not implemented here)

**Fix 1 (Mode A) — `ai_advisor.py:354–360`:** Expand `_SEC_KEY_CONCEPTS["Revenues"]` to a fallback list: try `RevenueFromContractWithCustomerExcludingAssessedTax` → `SalesRevenueNet` → `Revenues` in order, picking the one with the most recent `end`. This pattern applies to any concept that has migrated to a new GAAP tag over time.

**Fix 2 (Mode B) — `ai_advisor.py:1012`:** Change sort key from `e.get("filed", "") or ""` to `e.get("end", "") or ""`. This selects the most recent period-end regardless of comparative bundling. This fix is necessary and independently sufficient for all non-deprecated concepts.

**Both fixes are required.** Fix 2 alone does not recover MSFT Revenues (the tag is deprecated; no entry within it has `end` > 2010-06-30). Fix 1 alone leaves all non-Revenue concepts still stale by 1–2 years.

---

## Evidence Index

| Evidence | Type | Citation |
|---|---|---|
| Mode A root cause | Code citation | `ai_advisor.py:354–360` (`_SEC_KEY_CONCEPTS`) |
| Mode B root cause | Code citation | `ai_advisor.py:1008–1019` (sort key) |
| MSFT Revenues stale | Runnable result | HTTP 200 CIK0000789019; producer end=2007-09-30; correct (within tag) end=2010-06-30 |
| MSFT revenue-equivalent clincher | Runnable result | `RevenueFromContractWithCustomerExcludingAssessedTax` present, end=2025-06-30, 48 10-K entries |
| MSFT Mode B (non-Revenue concepts) | Runnable result | NetIncomeLoss producer end=2023-06-30 vs correct 2025-06-30; Assets/Liabilities 1yr gap; SE 2yr gap |
| JPM Mode B (control) | Runnable result | HTTP 200 CIK0000019617; all 5 concepts stale by 1–2yr; no revenue equivalents (Mode A N/A) |
| MSFT `Revenues` tag is deprecated | Runnable result | 15 10-K entries, all filed 2010-07-30, latest end=2010-06-30; `SalesRevenueNet` / `RevenueFromContractWithCustomerExcludingAssessedTax` present with current data |

**Confidence: HIGH** — both failure modes verified by live SEC data + code inspection + exact reproduction of observed render value.
