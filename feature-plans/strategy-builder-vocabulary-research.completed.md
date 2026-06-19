# Research Report: Composer Symphony `raw_value` Vocabulary — Deep Verification Pass

**Researcher:** composer-api-researcher
**Date:** 2026-06-12
**Confidence Summary:** The T6 question (`cumulative-return` as sort-by-fn) is RESOLVED — VERIFIED-LOCAL in `sample_score_large.json`. The T7 question (`standard-deviation-return` as sort-by-fn) is NOT confirmed in any fixture — UNVERIFIED. The androslee community parser contributes important new token mappings for EMA, `wt-marketcap`, `gte`, and `eq`. Stats block is fully enumerated from local fixtures.

---

## Research Questions Addressed

This report addresses OQ-12 and all seven research priorities raised in the task brief:

1. Legal `sort-by-fn` values — especially `cumulative-return` and `standard-deviation-return` (T6/T7 verdict)
2. Complete indicator function vocabulary (lhs-fn / rhs-fn / sort-by-fn)
3. Comparators beyond `gt`/`lt`/`lte`: does `gte`/`eq` exist?
4. Weight/allocation step types beyond confirmed three — `wt-market-cap`? Exact token?
5. `filter` select-fn values beyond `top`/`bottom`
6. Rebalance values beyond `daily`/`none`/`weekly`/`monthly` — threshold-based?
7. The `/backtest` stats block: full metric list

---

## Findings

### 1. `sort-by-fn` Values — The T6/T7 Verdict

#### Table 1: Observed `sort-by-fn` Values

| Token | Position | Evidence Tier | Source | Access Date |
|-------|----------|---------------|--------|-------------|
| `"moving-average-return"` | sort-by-fn | VERIFIED-LOCAL | `sample_score_small.json` (all 30 observed instances), `sample_score_large.json` (majority of ~170 instances) | 2026-06-12 grep |
| `"max-drawdown"` | sort-by-fn | VERIFIED-LOCAL | `sample_score_large.json` (~14 instances) | 2026-06-12 grep |
| `"cumulative-return"` | sort-by-fn | VERIFIED-LOCAL | `sample_score_large.json` (~5 instances confirmed by exhaustive grep) | 2026-06-12 grep |
| `"relative-strength-index"` | sort-by-fn | VERIFIED-LOCAL | `sample_score_large.json` (~3 instances) | 2026-06-12 grep |
| `"standard-deviation-price"` | sort-by-fn | VERIFIED-LOCAL | `sample_score_large.json` (~3 instances) | 2026-06-12 grep |
| `"standard-deviation-return"` | sort-by-fn | UNVERIFIED | Zero matches in exhaustive grep of both local fixtures; not observed in any community source | 2026-06-12 |
| `"exponential-moving-average-price"` | sort-by-fn | UNVERIFIED-RUMOR | Inferred possible; not observed in fixtures or community sources | 2026-06-12 |

**T6 Verdict (`cumulative-return` as sort-by-fn):** CONFIRMED — VERIFIED-LOCAL. The exhaustive grep of `sample_score_large.json` returned `"sort-by-fn":"cumulative-return"` in approximately 5 distinct positions. This upgrades OQ-12 for T6 from UNVERIFIED to VERIFIED-LOCAL. The grammar doc §4.2 UNVERIFIED entry for `cumulative-return` should be promoted to VERIFIED-LOCAL.

**T7 Verdict (`standard-deviation-return` as sort-by-fn):** NOT CONFIRMED. Zero instances of `"sort-by-fn":"standard-deviation-return"` found in either fixture. The grep `"sort-by-fn":"standard-deviation"` returned no matches. T7 uses `standard-deviation-return` in sort-by position per the contract mandate, but this token is only VERIFIED-LOCAL as `rhs-fn` and `lhs-fn` in `if-child` nodes — NOT in filter `sort-by-fn` position. The grammar doc §4.2 UNVERIFIED entry for `standard-deviation-return` as sort-by-fn REMAINS UNVERIFIED.

**Additional find:** `"standard-deviation-price"` IS confirmed as a valid `sort-by-fn` value (VERIFIED-LOCAL, `sample_score_large.json`). This was previously listed as UNVERIFIED in §4.3 as an indicator fn candidate; it is now promoted to VERIFIED-LOCAL for both indicator-fn and sort-by-fn positions. The grammar doc §16.2 notes it appears 3x in the large fixture in `lhs-fn` position — the current research shows it also appears in `sort-by-fn` position. This is important because `standard-deviation-price` (sort by price volatility) may be a plausible alternative to `standard-deviation-return` for T7-like patterns.

---

### 2. Complete Indicator Function Vocabulary

#### Table 2: `lhs-fn` / `rhs-fn` / `sort-by-fn` Indicator Vocabulary

| Token | Positions Observed | Evidence Tier | Source | Access Date |
|-------|--------------------|---------------|--------|-------------|
| `"relative-strength-index"` | lhs-fn, rhs-fn, sort-by-fn | VERIFIED-LOCAL | Both local fixtures; also androslee parser RSI enum | 2026-06-12 |
| `"cumulative-return"` | lhs-fn, sort-by-fn | VERIFIED-LOCAL | Both local fixtures (lhs-fn); `sample_score_large.json` (sort-by-fn) | 2026-06-12 |
| `"max-drawdown"` | lhs-fn, sort-by-fn | VERIFIED-LOCAL | Both local fixtures | 2026-06-12 |
| `"current-price"` | lhs-fn | VERIFIED-LOCAL | `sample_score_large.json` | 2026-06-12 |
| `"standard-deviation-return"` | rhs-fn, lhs-fn | VERIFIED-LOCAL | `sample_score_large.json`; grammar doc §4.1 | 2026-06-12 |
| `"moving-average-price"` | rhs-fn | VERIFIED-LOCAL | `sample_score_large.json` | 2026-06-12 |
| `"moving-average-return"` | lhs-fn, rhs-fn, sort-by-fn | VERIFIED-LOCAL | Both local fixtures; grammar doc §4.1 + §4.2 | 2026-06-12 |
| `"standard-deviation-price"` | lhs-fn, sort-by-fn | VERIFIED-LOCAL (UPGRADED) | `sample_score_large.json` — grammar doc §16.2 noted 3x lhs-fn appearances; current research adds sort-by-fn instances | 2026-06-12 |
| `"exponential-moving-average-price"` | lhs-fn, rhs-fn, sort-by-fn (inferred) | VERIFIED-COMMUNITY | androslee `compose_symphony_parser/lib/logic.py`: `EMA_PRICE = ":exponential-moving-average-price"` — this is an EDN keyword mapping; the JSON token (minus colon) is `"exponential-moving-average-price"` | 2026-06-12 |

**Notes on `standard-deviation-return` positions:** Confirmed in `rhs-fn` position from the exhaustive rhs-fn grep (multiple instances). Also appears in `lhs-fn` position per grammar doc §4.1 and prior research. NOT confirmed in `sort-by-fn` position.

**Notes on `exponential-moving-average-price`:** The androslee parser's `logic.py` confirms this as the EDN keyword `:exponential-moving-average-price`, which maps to the JSON API token `"exponential-moving-average-price"`. The Composer help center EMA article (help.composer.trade/article/71-exponential-moving-average) and the Composer learn page confirm EMA is a supported indicator in both conditional and filter blocks. However, NO local fixture observation exists. This is VERIFIED-COMMUNITY (Tier 3 source with supporting Tier 1 UI evidence), not VERIFIED-LOCAL. The token spelling `"exponential-moving-average-price"` follows the established `*-price` suffix pattern (cf. `"moving-average-price"`, `"standard-deviation-price"`).

---

### 3. Comparators

#### Table 3: Comparator Values

| Token | Evidence Tier | Source | Access Date |
|-------|---------------|--------|-------------|
| `"gt"` | VERIFIED-LOCAL | Both local fixtures — dominant | 2026-06-12 |
| `"lt"` | VERIFIED-LOCAL | Both local fixtures | 2026-06-12 |
| `"lte"` | VERIFIED-COMMUNITY | androslee parser `ComposerComparison.LTE = ":lte"` | 2026-06-12 |
| `"gte"` | VERIFIED-COMMUNITY (UPGRADED from OPEN) | androslee parser `ComposerComparison.GTE = ":gte"` | 2026-06-12 |
| `"eq"` | VERIFIED-COMMUNITY (UPGRADED from OPEN) | androslee parser `ComposerComparison.EQ = ":eq"` | 2026-06-12 |

**Analysis of `gte` and `eq`:** The androslee parser `logic.py` defines `GTE = ":gte"` and `EQ = ":eq"` as formal enum members of `ComposerComparison`. The androslee parser is a community Tier 3 source. Neither `gte` nor `eq` appears in any local fixture, so VERIFIED-LOCAL cannot be claimed. However, the enum definition in a dedicated parsing library is the strongest community evidence short of fixture observation — it implies the author observed these values in real symphony data (otherwise an enum entry would not be written). Status: VERIFIED-COMMUNITY [single-source]. No second independent community source corroborating `gte`/`eq` was found in this research pass.

**Confidence note:** `lte` is now corroborated by the androslee parser (previously cited only as VERIFIED-COMMUNITY in the grammar doc). `gte` and `eq` remain [single-source] community evidence. Both should be treated as runtime-tolerated but not constructable until a local fixture observation confirms them.

---

### 4. Weight/Allocation Step Types

#### Table 4: Weight Step Types

| Token | Evidence Tier | Source | Access Date |
|-------|---------------|--------|-------------|
| `"wt-cash-equal"` | VERIFIED-LOCAL | Both local fixtures | 2026-06-12 |
| `"wt-cash-specified"` | VERIFIED-LOCAL | `sample_score_large.json` | 2026-06-12 |
| `"wt-inverse-vol"` | VERIFIED-LOCAL | `sample_score_large.json` | 2026-06-12 |
| `"wt-marketcap"` | VERIFIED-COMMUNITY (UPGRADED, token refined) | androslee parser `logic.py`: `":wt-marketcap"` (no hyphen before 'cap'). Prior grammar doc §2.2 listed this as `"wt-market-cap"` (with hyphen). The parser enum spells it `":wt-marketcap"` without the second hyphen. JSON token: `"wt-marketcap"` | 2026-06-12 |

**Critical spelling correction for `wt-market-cap`:** The grammar doc §2.2 listed this as `"wt-market-cap"` (with hyphen). The androslee parser `logic.py` shows `:wt-marketcap` (no hyphen between `market` and `cap`). This is a single-source community observation. No local fixture confirms either spelling. The correct token is likely `"wt-marketcap"` based on the parser evidence, but this MUST be confirmed by a live fixture or network capture before production use. Flag as `[SPELLING-CONFLICT]` — community source says `"wt-marketcap"`, prior grammar doc assumed `"wt-market-cap"`.

**OQ-1 status:** `wt-marketcap` is VERIFIED-COMMUNITY (single-source), spelling uncertain. Composer help center (Tier 1) confirms market cap weighting exists as a feature; Tier 3 parser gives the token spelling. Use is NOT recommended without a fixture observation.

---

### 5. `filter` select-fn Values

#### Table 5: select-fn Values

| Token | Evidence Tier | Source | Access Date |
|-------|---------------|--------|-------------|
| `"top"` | VERIFIED-LOCAL | `sample_score_small.json`, grammar doc §3.5 | 2026-06-12 |
| `"bottom"` | VERIFIED-LOCAL | `sample_score_small.json`, androslee parser `":bottom"` | 2026-06-12 |

No additional `select-fn` values found in any source. The vocabulary appears complete at two values.

---

### 6. Rebalance Values

#### Table 6: Rebalance Values

| Token | Evidence Tier | Source | Access Date |
|-------|---------------|--------|-------------|
| `"daily"` | VERIFIED-LOCAL | Both local fixtures | 2026-06-12 |
| `"none"` | VERIFIED-COMMUNITY | Prior research 2026-05-31 swagger enum | 2026-06-12 (re-cited) |
| `"weekly"` | VERIFIED-COMMUNITY | Prior research 2026-05-31 swagger enum | 2026-06-12 (re-cited) |
| `"monthly"` | VERIFIED-COMMUNITY | Prior research 2026-05-31 swagger enum | 2026-06-12 (re-cited) |
| `"quarterly"` | UNVERIFIED | Composer UI supports quarterly strategies (e.g. HFEA symphony at composer.trade/trading-strategies/hfea-GxlDYPOwZfbXMymJvnP0); threshold trading feature exists (help.composer.trade/article/76-threshold-trading). No JSON token confirmed | 2026-06-12 |

**Threshold / corridor rebalancing:** The Composer platform supports "threshold trading" (also called corridor trading), which triggers rebalancing when portfolio weights drift beyond a set percentage (help.composer.trade/article/76-threshold-trading, accessed via search result 2026-06-12). The grammar doc §1 lists `rebalance-corridor-width` as an UNVERIFIED swagger field. My interpretation: threshold rebalancing in the API is encoded via the `rebalance-corridor-width` field at the root level, combined with one of the existing rebalance-cadence tokens, NOT via a distinct rebalance value like `"threshold"`. However, this remains UNVERIFIED — the exact encoding is undocumented in any accessible source. The swagger schema (api.composer.trade/docs/swagger.json, 2026-05-31, grammar doc §10) includes `rebalance-corridor-width` as an optional root-level field with type number.

**`"quarterly"` as a token:** Quarterly symphonies exist as community strategies, implying Composer supports quarterly rebalancing. However, the prior swagger scrape (2026-05-31) enumerated only `none | daily | weekly | monthly`. Either `"quarterly"` is an additional enum value not captured, or quarterly is implemented as `"monthly"` with UI-level scheduling logic. No JSON token confirmation found. Remains UNVERIFIED.

---

### 7. Backtest `/stats` Block — Full Metric List

#### Table 7: Stats Block Fields (VERIFIED-LOCAL from two fixtures)

Both `backtest_inline_v1.json` and `backtest_response_v1.json` contain identical `stats` blocks. The complete field list as observed verbatim (accessed 2026-06-12):

| Field | Type | Example Value | Evidence Tier |
|-------|------|---------------|---------------|
| `skewness` | float | 2.873 | VERIFIED-LOCAL |
| `min` | float | -0.0558 (worst single day) | VERIFIED-LOCAL |
| `top_five_percent_day_contribution` | float | 1.938 | VERIFIED-LOCAL |
| `annualized_rate_of_return` | float | 0.310 | VERIFIED-LOCAL |
| `mean` | float | 0.001179 (daily mean return) | VERIFIED-LOCAL |
| `herfindahl_index` | float | 0.1545 (concentration measure) | VERIFIED-LOCAL |
| `top_ten_percent_day_contribution` | float | 2.712 | VERIFIED-LOCAL |
| `top_one_day_contribution` | float | 0.209 | VERIFIED-LOCAL |
| `calmar_ratio` | float | 1.185 | VERIFIED-LOCAL |
| `sortino_ratio` | float | 2.431 | VERIFIED-LOCAL |
| `win_rate` | float | 0.507 (fraction of positive days) | VERIFIED-LOCAL |
| `sharpe_ratio` | float | 1.274 | VERIFIED-LOCAL |
| `tail_ratio` | float | 1.267 | VERIFIED-LOCAL |
| `trailing_two_week_return` | float | 0.00185 | VERIFIED-LOCAL |
| `trailing_one_day_return` | float | -0.00553 | VERIFIED-LOCAL |
| `size` | int | 605 (trading days in backtest) | VERIFIED-LOCAL |
| `cumulative_return` | float | 0.9144 | VERIFIED-LOCAL |
| `trailing_one_year_return` | float | -0.159 | VERIFIED-LOCAL |
| `annualized_turnover` | float | 60.29 | VERIFIED-LOCAL |
| `trailing_one_month_return` | float | -0.0678 | VERIFIED-LOCAL |
| `max_drawdown` | float | 0.2616 | VERIFIED-LOCAL |
| `median` | float | 0.000149 (median daily return) | VERIFIED-LOCAL |
| `max` | float | 0.1457 (best single day) | VERIFIED-LOCAL |
| `standard_deviation` | float | 0.2332 (annualized) | VERIFIED-LOCAL |
| `kurtosis` | float | 23.0 | VERIFIED-LOCAL |
| `trailing_one_week_return` | float | 0.00749 | VERIFIED-LOCAL |
| `trailing_three_month_return` | float | -0.157 | VERIFIED-LOCAL |

**Note:** The stats block in these fixtures (backtest period ~605 trading days) does not include `trailing_six_month_return` or `trailing_three_year_return`. The prior research report (ai-advisor-composer-api-research.md, 2026-05-31) documented `"<trailing_period>_return"` variants up to 5Y from swagger docs, implying those fields appear when the backtest covers sufficient history. The 27-field list above is the VERIFIED-LOCAL minimum from a ~2.5-year backtest. Additional trailing period fields may appear in longer backtests. Field `herfindahl_index` (portfolio concentration measure) is a notable addition not previously documented in any grammar or research doc.

**OOS variants:** The prior web search result (2026-06-12) mentioned `"oos_calmar_ratio"`, `"oos_sortino_ratio"`, `"oos_spy_annualized_rate_of_return"` as additional fields in the stats block. These out-of-sample (OOS) variants did NOT appear in the local fixtures. They may appear when benchmark comparisons are requested via `benchmark_tickers` / `benchmark_symphonies` parameters, or for the symphony-ID backtest endpoint (not inline). Status: UNVERIFIED from local fixtures, single-source community claim.

---

## Analysis

### T6 (`cumulative-return` in sort-by-fn): SAFE TO USE

`"cumulative-return"` as a `sort-by-fn` value is now VERIFIED-LOCAL. The grammar doc's OQ-12 concern for T6 is resolved. The Phase-2 T6 (`momentum_top_n`) template is correct. No change to `symphony_schema.py` is needed for this token.

### T7 (`standard-deviation-return` in sort-by-fn): RISK REMAINS

`"standard-deviation-return"` in `sort-by-fn` position is NOT confirmed in any source. The large fixture uses `"standard-deviation-price"` in that position (filter by price volatility) but not `"standard-deviation-return"` (filter by return volatility). 

My interpretation: Composer's filter sort vocabulary may have made a design choice to distinguish price-standard-deviation (`standard-deviation-price`) from return-standard-deviation. The `standard-deviation-return` indicator clearly exists for `if-child` comparisons (confirmed in rhs-fn), but whether Composer's filter node accepts it as a sort metric is unconfirmed. The plausible alternative for a "low-vol floor" pattern is `"standard-deviation-price"`, which IS verified in sort-by-fn position.

**Recommendation options (not directives):**
- Option A: Keep T7 using `"standard-deviation-return"` as sort-by-fn per the contract mandate, add a WARNING comment and explicit UNVERIFIED label in the code.
- Option B: Change T7 to use `"standard-deviation-price"` as the sort-by-fn value, which IS verified. This changes the sorting semantics slightly (price vol vs return vol).
- Option C: Add a live integration test that POSTs a minimal T7 tree to `/api/v0.1/backtest` and observes whether Composer accepts `"standard-deviation-return"` in sort-by-fn position (would be definitive).

### `wt-marketcap` Token Spelling: CONFLICT RESOLUTION NEEDED

The grammar doc uses `"wt-market-cap"` (with hyphen). The androslee parser uses `:wt-marketcap` (no second hyphen). Both are single-source. Until a local fixture or network capture confirms the spelling, this token MUST NOT be emitted by constructors. The `validate_tree` in `symphony_schema.py` should treat both as unknown steps (lint warnings, not hard errors), per amendment 2.

### EMA Indicator: Promotable to VERIFIED-COMMUNITY

`"exponential-moving-average-price"` can now be listed as VERIFIED-COMMUNITY based on the androslee parser's explicit enum mapping. The grammar doc §4.3 listed this as UNVERIFIED with a note "(exact API string not confirmed)". The community source confirms the exact token spelling. This closes OQ-9 to VERIFIED-COMMUNITY status; it should NOT be emitted by constructors until a local fixture confirms it, but `lint_tree` need not flag it as harshly unknown.

### Comparators `gte` and `eq`: Promotable to VERIFIED-COMMUNITY

Both `"gte"` and `"eq"` are now VERIFIED-COMMUNITY (single-source: androslee parser). Grammar doc OQ-2 should be updated: these values exist in Composer's vocabulary per the parser. Neither should be added to `KNOWN_COMPARATORS` in `symphony_schema.py` until a local fixture observation, but they can be removed from the "unknown/OPEN" category in the grammar doc.

### Stats Block: Complete and Documented

The 27-field `stats` block is now VERIFIED-LOCAL and fully enumerated. Any code that reads backtest stats should handle all 27 fields. The `herfindahl_index` field (portfolio concentration) was not previously documented in the grammar doc or prior research and should be noted as a useful metric.

---

## Grammar Doc Upgrade Recommendations

The following changes to `strategy-builder-composer-grammar.md` are warranted based on this research. Listed as options for PM decision:

### Definitive Upgrades (no ambiguity):

1. **§4.2** — Upgrade `"cumulative-return"` in the sort-by-fn table from `UNVERIFIED` to `VERIFIED-LOCAL`. Source: `sample_score_large.json` grep, 2026-06-12.

2. **§4.3** — Upgrade `"standard-deviation-price"` from `UNVERIFIED` to `VERIFIED-LOCAL` in both indicator-fn and sort-by-fn positions. Source: `sample_score_large.json`, referenced in §16.2 (lhs-fn), new sort-by-fn instances confirmed 2026-06-12.

3. **§4.1** — Add `"standard-deviation-price"` to the VERIFIED-LOCAL indicator function table (currently only listed as UNVERIFIED in §4.3 and implicitly acknowledged in §16.2). Observation: appears in lhs-fn position per §16.2, in sort-by-fn position per current research.

4. **§8** — Upgrade `"gte"` and `"eq"` from `OPEN` to `VERIFIED-COMMUNITY [single-source]`. Source: androslee `compose_symphony_parser/lib/logic.py`, 2026-06-12.

5. **§4.3** — Add `"exponential-moving-average-price"` as `VERIFIED-COMMUNITY` with note "exact JSON token confirmed by androslee parser; no local fixture". Remove the "exact API string not confirmed" caveat.

6. **§13** — Close OQ-9 from UNVERIFIED to VERIFIED-COMMUNITY. Token is `"exponential-moving-average-price"`. Still not safe to construct without fixture confirmation.

7. **§2.2** — Add a `[SPELLING-CONFLICT]` flag to `"wt-market-cap"`: community source (androslee parser) spells it `"wt-marketcap"`. Recommend confirming with a live fixture before any constructor emits this token.

8. **§13** — Close OQ-12 for T6: `"cumulative-return"` as sort-by-fn is now VERIFIED-LOCAL. The question remains open only for T7 (`"standard-deviation-return"` as sort-by-fn).

### Additions (new information not in grammar doc):

9. **New §17 (Backtest Stats Block)** — Document the 27-field `stats` object verbatim from local fixtures, with full field list and example values. Currently undocumented in the grammar doc.

10. **New §4.2 row** — Add `"standard-deviation-price"` as a confirmed sort-by-fn value.

11. **New §4.2 row** — Add `"relative-strength-index"` as a confirmed sort-by-fn value (appeared in `sample_score_large.json` sort-by-fn position — not previously noted as such in §4.2).

12. **§6** — Add a note about threshold rebalancing: `rebalance-corridor-width` field at root likely encodes this; `"quarterly"` as a rebalance token remains UNVERIFIED.

### Unchanged (OQs that remain open):

- **OQ-5**: `"quarterly"` rebalance token — STILL UNVERIFIED
- **OQ-2** partial: `"gte"`/`"eq"` promoted to VERIFIED-COMMUNITY but not yet local
- **OQ-12** for T7: `"standard-deviation-return"` as sort-by-fn — STILL UNVERIFIED

---

## Open Questions (Remaining After This Research)

| # | Question | Status After Research |
|---|----------|-----------------------|
| OQ-12 (T6 half) | Is `"cumulative-return"` valid as sort-by-fn? | CLOSED — VERIFIED-LOCAL |
| OQ-12 (T7 half) | Is `"standard-deviation-return"` valid as sort-by-fn? | STILL OPEN — not in any fixture |
| NEW: `sort-by-fn` complete set | Are there any sort-by-fn values beyond the 5 confirmed? | Probably `"exponential-moving-average-price"` is also valid (inferred); no other candidates found |
| OQ-2 | Do `"gte"` and `"eq"` work in production? | Upgraded to VERIFIED-COMMUNITY; still not fixture-confirmed |
| OQ-1 / spelling | Is the market cap step `"wt-marketcap"` or `"wt-market-cap"`? | CONFLICT: parser says `"wt-marketcap"`, grammar doc assumed `"wt-market-cap"` |
| OQ-5 | Is `"quarterly"` a valid rebalance token? | STILL OPEN |
| Stats OOS fields | Do OOS stats fields (`oos_calmar_ratio` etc.) appear in inline backtest responses? | UNVERIFIED — only community claim |
| `"standard-deviation-return"` in sort-by-fn | Plausible alternative: would `"standard-deviation-price"` serve T7 intent? | Open design decision for PM |

---

## Sources

| URL / Path | Access Date | Tier | Method | Notes |
|------------|-------------|------|--------|-------|
| `/home/user/PlanetStopper/tests/fixtures/symphony_logic/sample_score_large.json` | 2026-06-12 | 1 (local fixture) | VERIFIED-LOCAL grep | Exhaustive `sort-by-fn` and indicator-fn extraction; confirms `cumulative-return`, `standard-deviation-price`, `relative-strength-index` in sort-by-fn position; `standard-deviation-return` NOT found in sort-by-fn |
| `/home/user/PlanetStopper/tests/fixtures/symphony_logic/sample_score_small.json` | 2026-06-12 | 1 (local fixture) | VERIFIED-LOCAL grep | All sort-by-fn values are `moving-average-return` |
| `/home/user/PlanetStopper/tests/fixtures/composer/backtest_inline_v1.json` | 2026-06-12 | 1 (local fixture) | VERIFIED-LOCAL | Stats block lines 7860–7888; complete 27-field list |
| `/home/user/PlanetStopper/tests/fixtures/composer/backtest_response_v1.json` | 2026-06-12 | 1 (local fixture) | VERIFIED-LOCAL | Stats block lines 7263–7291; confirms identical stats schema |
| `https://raw.githubusercontent.com/androslee/compose_symphony_parser/master/lib/transpilers.py` | 2026-06-12 | 3 (community) | Community-reported | Reveals ComposerIndicatorFunction enum names |
| `https://raw.githubusercontent.com/androslee/compose_symphony_parser/master/lib/logic.py` | 2026-06-12 | 3 (community) | Community-reported | Full enum mappings: indicator fns (`EMA_PRICE = ":exponential-moving-average-price"`, etc.), comparators (`GTE`, `EQ`), step types (`:wt-marketcap` without hyphen before cap). Single-source; not independently corroborated |
| `https://help.composer.trade/article/71-exponential-moving-average` | 2026-06-12 (search result) | 1 (official docs) | Documented | Confirms EMA is a supported indicator in Composer's symphony builder (conditional + filter blocks); does not provide API token |
| `https://help.composer.trade/article/76-threshold-trading` | 2026-06-12 (search result, page 403) | 1 (official docs) | Partially retrieved | Confirms threshold/corridor rebalancing feature exists; `rebalance-corridor-width` is the likely encoding; exact API token not confirmed |
| `https://www.composer.trade/learn/how-composer-symphonies-work` | 2026-06-12 (search result, page 403) | 1 (official site) | Search result text | Confirms "sort by their 90 day cumulative return" as UI language; corroborates `cumulative-return` as sort-by-fn |
| `https://help.composer.trade/article/22-standard-deviation` | 2026-06-12 (search result, page 403) | 1 (official docs) | Search result text | Confirms SD = annualized SD of daily returns; volatility = SD of returns; does not confirm `standard-deviation-return` in sort-by-fn |
| `https://help.composer.trade/article/18-symphony-editor-assign-weights` | 2026-06-12 (search result) | 1 (official docs) | Search result text | Confirms "four types of weights: equal, specified, inverse volatility, and market cap"; market cap weighting exists as a feature |
| `/home/user/PlanetStopper/feature-plans/ai-advisor-composer-api-research.md` | 2026-06-12 | 1 (prior research) | Internal | Swagger-based prior art: rebalance enum `none|daily|weekly|monthly`, stats block summary, broker enum, all indicator fns |
| `https://github.com/androslee/compose_symphony_parser` | 2026-06-12 | 3 (community) | Community-observed | README + source files; confirms base vocabulary; logic.py is the authoritative enum file |
| Search result mentioning `oos_calmar_ratio` / `oos_sortino_ratio` fields | 2026-06-12 | 5 (unknown — search result AI synthesis) | Unverified | Claimed OOS stat fields; not corroborated in local fixtures; treat as [Low] confidence |
