# Composer Symphony Grammar Reference — v2 (Corpus-Validated)

**Author:** general-purpose research agent (corpus mining + doc cross-reference)
**Date:** 2026-06-14
**Supersedes (does not replace):** `strategy-builder-composer-grammar.md` (v1, 2026-06-11). v1 was built from 2 local `/score` fixtures + community sources and left ~11 OPEN questions. This v2 resolves the grammar against **ground truth: 10,441 parsed real Composer symphonies** mined from the `captplanet` MongoDB `strategies` collection (`edn_string` field).

---

## 0. Executive Summary

- **The headline (compound ANY/ALL):** ANY-vs-ALL is selected by the **`operator`** key inside a node's **`condition`** block. `"operator":"any"` = OR-gate; `"operator":"all"` = AND-gate. There is **no third value** — `operator` is only ever `"any"` or `"all"` across the entire corpus. The `condition` block is a self-contained, recursively-nestable predicate tree (`condition-type` ∈ {`compound`, `binary`, `binary-compound`}) that lives ALONGSIDE the legacy flat `lhs-fn`/`comparator`/`rhs-*` keys on the same `if-child`. **Real ANY-gate and ALL-gate examples are quoted verbatim in §7.**
- **Corpus is JSON, not keyword-EDN.** Despite the field name `edn_string`, every document is **JSON-serialized** (string keys: `"step"`, `"if-child"`, `"is-else-condition?"`). Zero documents use Clojure keyword EDN (`:step`). So v1's JSON-centric description is the correct serialization to document; there is no separate EDN form to reconcile. (10,441/10,441 parse as JSON; the codebase walkers in `symphony_logic.py` / `symphony_schema.py` already assume this JSON form.)
- **v1 OPEN questions resolved: 9 of 11** (OQ-1, OQ-2, OQ-4, OQ-5, OQ-8, OQ-9 fully; OQ-7, OQ-10 informed; OQ-3, OQ-6, OQ-11 remain backend-behavior unknowns the corpus can't answer).
- **Biggest constructor gap:** `advisors/symphony_schema.py`'s `make_if`/`make_condition` **cannot emit a compound ANY/ALL `condition` block at all** — they only produce a single flat condition. Any objective that needs "if ALL of X,Y,Z" or "if ANY of X,Y,Z" is inexpressible today. Details in §11.

### Evidence labels
- **VERIFIED-CORPUS (n=N)** — observed in the MongoDB corpus with a frequency count.
- **VERIFIED-DOC** — confirmed by Composer's official help center / API docs (URL + date).
- **VERIFIED-LOCAL** — from repo fixtures/code (carried from v1).
- **OPEN** — unresolved (backend behavior the corpus cannot reveal).

### Corpus provenance
- Collection `captplanet.strategies`: 11,164 documents; 10,441 had a non-empty parseable `edn_string` (723 empty). All frequency counts below are **node-occurrence counts across all 10,441 parsed symphonies** unless stated as document counts.
- Mined 2026-06-14 via single-pass full-tree walk (`MONGO_COLLECTION=captplanet`). Secrets never logged.

---

## 1. Top-Level (`root`) Shape — VERIFIED-CORPUS

Every parsed symphony root carries these 6 fields in 100% of documents (n=10,441 each):

| Field | Type | n | Notes |
|-------|------|---|-------|
| `step` | `"root"` | 10,441 | always `"root"` |
| `id` | string | 10,441 | see §9 |
| `name` | string | 10,441 | |
| `description` | string | 10,441 | e.g. `"(Created with Composer AI)"` — **always present** (v1 listed it only as a cosmetic extra) |
| `rebalance` | string | 10,441 | see §6 |
| `children` | array | 10,441 | |

Optional root fields, with real frequencies (resolves v1's "UNVERIFIED" swagger fields):

| Field | n (docs) | Resolution |
|-------|----------|------------|
| `asset_class` | 5,996 | VERIFIED-CORPUS — real, common. Singular string. |
| `asset_classes` | 4,131 | VERIFIED-CORPUS — real, common. Array. |
| `rebalance-corridor-width` | 2,593 | VERIFIED-CORPUS — threshold-rebalance drift band (e.g. `0.05`). **Always pairs with `rebalance:"none"`** (exactly n=2,593 corridor vs n=2,593 `rebalance:"none"`). Resolves v1 OQ-10. |
| `collapsed?` | 765 | cosmetic (editor UI) |
| `color` | 504 | cosmetic |
| `sparkgraph_url` | 504 | cosmetic |
| `suppress_incomplete_warnings` | 47 | cosmetic |
| `collapsed-specified-weight?` | 18 | cosmetic |

---

## 2. `step` Vocabulary — VERIFIED-CORPUS (full census)

All 9 step values, by node-occurrence count across the corpus:

| Step | n (node occurrences) | Notes |
|------|---------------------|-------|
| `asset` | 5,246,719 | leaf |
| `if-child` | 3,862,632 | true/else branch |
| `wt-cash-equal` | 2,997,340 | |
| `if` | 1,931,316 | |
| `group` | 1,425,246 | |
| `filter` | 508,808 | |
| `wt-cash-specified` | 164,781 | |
| `wt-inverse-vol` | 51,467 | |
| `root` | 10,441 | one per symphony |

**There is NO `wt-market-cap` step** — 0 occurrences in 10,441 symphonies (resolves v1 OQ-1). Market-cap weighting is NOT a step type. The v1 `KNOWN_STEPS` frozenset of 9 is therefore **complete and correct** — do not add `wt-market-cap`.

**Market-cap weighting was NOT found in the corpus at all.** Composer's help docs list "Market cap weighting" as the 4th weight scheme (VERIFIED-DOC, §3), so the capability exists in the product, but no node in the corpus encodes it. The `weight-method` field that DOES appear (n=572 on group nodes) carries values **`percentage`** (n=259) and **`fraction`** (n=131) in a 200-doc sample — these are specified-weight sub-modes (how a `wt-cash-specified` allocation is expressed: whole percents vs fractions), **not** market-cap. So: market-cap weighting is real per docs but **effectively unused / unobserved** in this 10,441-symphony corpus; its exact serialization remains **OPEN** (likely a `weight-method` value or a step variant not present here). `weight-method` (percentage/fraction) is itself a previously-undocumented field — see §10.

---

## 3. Weight Types & Encoding — VERIFIED-CORPUS + VERIFIED-DOC

**Composer's 4 documented weight schemes** (VERIFIED-DOC, help.composer.trade/article/54, 2026-06-14): Equal, Specified, Inverse-volatility, **Market-cap** ("equities only"). Their corpus encodings:

| Scheme | Encoding | Evidence |
|--------|----------|----------|
| Equal | `step:"wt-cash-equal"` | VERIFIED-CORPUS (n=2,997,340) |
| Specified | `step:"wt-cash-specified"` + per-child `weight`; sub-mode in `weight-method` ∈ {`percentage`,`fraction`} | VERIFIED-CORPUS (step n=164,781; weight-method n=572) |
| Inverse-vol | `step:"wt-inverse-vol"` + `window-days` | VERIFIED-CORPUS (n=51,467); **`window-days` present on 100% of these nodes** (51,467/51,467) — resolves v1 OQ-8: inverse-vol DOES carry a param (`window-days`, a flat int), it is NOT param-less. |
| Market-cap | **not found in corpus** | VERIFIED-DOC only (capability exists); corpus encoding OPEN — see §2 |

**`weight` object shape — VERIFIED-CORPUS (n=1,313,767, single uniform shape):**
```json
"weight": {"num": <number-or-numeric-string>, "den": <100-or-"100">}
```
- **Only ever `{num, den}`** — no other key combination appears in 1.3M weight objects.
- `den` represents 100 (API docs type it as string; corpus carries both int `100` and string `"100"`). VERIFIED-DOC (api.composer.trade: `weight:{num:string, den:string}`).
- `weight` appears on **many node types**, not just `wt-cash-specified` children: `if` (529,439), `asset` (406,051), `group` (223,881), `filter` (144,180), `wt-inverse-vol` (5,034), `wt-cash-equal` (4,213), `wt-cash-specified` (969). Confirms v1 amendment 4.

**Weight-sum constraint (v1 OQ-4):** Not enforceable as a hard rule from the corpus — `num` is routinely a fractional string (`"66.67"`) and weights live on heterogeneous node types. The constructor-side lint (`_EXPECTED_WEIGHT_SUM=100`) is a reasonable advisory but **must not be a hard error** (real trees violate a naive 100-sum across mixed node types). OQ-4 resolved: **no hard sum gate**; advisory only.

---

## 4. Indicator / `fn` Vocabulary — VERIFIED-CORPUS (full census)

`lhs-fn` / `rhs-fn` values across the corpus (node-occurrence counts):

| fn | n | In v1 KNOWN_INDICATOR_FNS? |
|----|---|----------------------------|
| `relative-strength-index` | 1,770,260 | yes |
| `cumulative-return` | 305,911 | yes |
| `moving-average-price` | 290,888 | yes |
| `current-price` | 200,521 | yes |
| `moving-average-return` | 151,332 | yes |
| `standard-deviation-return` | 59,887 | yes |
| `max-drawdown` | 58,839 | yes |
| **`exponential-moving-average-price`** | **45,816** | **NO** — v1 OQ-9 said "do not generate until confirmed". **CONFIRMED real.** Resolves OQ-9. |
| **`standard-deviation-price`** | **5,572** | **NO** — v1 §4.3 UNVERIFIED. **CONFIRMED real.** |
| **`percentage-price-oscillator-signal`** | **100** | **NO** — new, undocumented in v1 |
| **`percentage-price-oscillator`** | **99** | **NO** — new, undocumented in v1 |

So the **complete real `lhs/rhs-fn` set is 11 values** (v1 had 7 confirmed + 2 unverified; corpus adds PPO + PPO-signal and confirms EMA-price + std-dev-price). The `rsi` abbreviation never appears — canonical RSI token is always the full `relative-strength-index`.

**Indicator param keys — VERIFIED-CORPUS** (resolves v1's "only window?"):

| param key | n | Used by |
|-----------|---|---------|
| `window` | 2,097,975 | nearly all indicators |
| `short-window` | 204 | PPO (`percentage-price-oscillator`) |
| `long-window` | 204 | PPO |
| `smooth-window` | 101 | PPO-signal |
| `standard-deviations` | 2 | Bollinger (see §4b) |

So **`window` is NOT the sole param** — PPO uses `short-window`/`long-window`/`smooth-window`; Bollinger uses `standard-deviations`. v1's "only window observed" is **refuted** for these advanced indicators.

### 4b. `sort-by-fn` Vocabulary — VERIFIED-CORPUS (resolves v1 §4.2 / OQ-12 with full census)

Real `sort-by-fn` values (filter sort key), full corpus:

| sort-by-fn | n |
|-----------|---|
| `relative-strength-index` | 158,164 |
| `moving-average-return` | 143,948 |
| `cumulative-return` | 108,028 |
| `standard-deviation-return` | **65,197** |
| `max-drawdown` | 27,214 |
| `standard-deviation-price` | 3,975 |
| `exponential-moving-average-price` | 1,997 |
| `moving-average-price` | 257 |
| `current-price` | 21 |
| `percentage-price-oscillator` | 4 |
| `upper-bollinger` | 1 |
| `percentage-price-oscillator-signal` | 1 |
| `lower-bollinger` | 1 |

**Correction to v1:** v1 §4.2 / OQ-12 **REFUTED** `standard-deviation-return` as a sort-by-fn ("zero occurrences"). The full corpus shows **`standard-deviation-return` is a valid sort-by-fn with n=65,197** — v1's refutation was a fixture-sample artifact (only 2 fixtures). The Phase-2 T7 template switch away from it was unnecessary on grammar grounds (though harmless). Also newly confirmed in sort position: `relative-strength-index`, `exponential-moving-average-price`, `standard-deviation-price`, `current-price`, `moving-average-price`, PPO, and `upper-bollinger`/`lower-bollinger` (the only appearances of the Bollinger fns in the corpus).

---

## 5. `filter` Semantics — VERIFIED-CORPUS

`select-fn` values (full census): `top` (271,260), `bottom` (237,548). **Only these two.** Both required: every filter node (n=508,808) carries `select-fn`, `select-n`, and `sort-by-fn` (100%).

`select-n` distribution (top values): `1` (398,394 — dominant), `2` (70,882), `3` (30,641), `4` (6,545), `5` (1,414), then a long thin tail up to `420`. Carried as a **numeric string** in the corpus.

Filter also carries (newly documented vs v1): `select?` (n=183,245) and `sort-by?` (n=183,245) booleans, and the legacy flat `sort-by-window-days` (n=201,349) **alongside** `sort-by-fn-params` (n=309,353). Constructors should keep emitting the `sort-by-fn-params:{window}` object form.

---

## 6. Rebalance — VERIFIED-CORPUS (resolves v1 OQ-5)

Full census of `rebalance` values:

| Value | n (docs) | In v1? |
|-------|----------|--------|
| `daily` | 7,528 | yes |
| `none` | 2,593 | yes (threshold form — pairs with `rebalance-corridor-width`) |
| `monthly` | 129 | yes |
| `weekly` | 106 | yes |
| **`quarterly`** | **58** | **NO — v1 OQ-5 asked; CONFIRMED real** |
| **`yearly`** | **27** | **NO — newly discovered** |

**Resolution of OQ-5:** the rebalance enum is **`{daily, weekly, monthly, quarterly, yearly, none}`** (6 values). Threshold ("corridor") rebalancing is expressed as `rebalance:"none"` + `rebalance-corridor-width:<float>` (VERIFIED-CORPUS n=2,593, an exact 1:1 pairing with `rebalance:"none"`; VERIFIED-DOC help.composer.trade/article/76 "corridor trading"). **Recommendation:** extend `symphony_schema.KNOWN_REBALANCE` from `{daily,none,weekly,monthly}` to add `quarterly` and `yearly`.

---

## 7. Compound Conditions — THE HEADLINE — VERIFIED-CORPUS

### 7.1 How ANY vs ALL is encoded

When an `if-child` uses compound logic, it carries a **`condition`** object (in addition to its legacy flat keys). The `condition` block is a recursive predicate tree with three node kinds (`condition-type`):

- **`compound`** — a wrapper holding a `conditions: [...]` array and an **`operator`** that joins them. `operator:"all"` = AND; `operator:"any"` = OR.
- **`binary`** — a single leaf comparison: `lhs` / `comparator` / `rhs`.
- **`binary-compound`** — a single comparison broadcast over a `tickers: [...]` list, with its OWN `operator` (`any`/`all`). This is the **expression-level ANY/ALL** Composer's docs describe ("RSI of ANY(TQQQ, VTV, XLF) > X"). The `lhs.ticker` is the placeholder `"%"`, substituted by each ticker in `tickers`.

**`operator` value census (all nesting levels), VERIFIED-CORPUS:**

| operator | n | meaning |
|----------|---|---------|
| `any` | 9,655 | OR-gate |
| `all` | 667 | AND-gate |

**`condition-type` value census, VERIFIED-CORPUS:**

| condition-type | n |
|----------------|---|
| `binary-compound` | 20,420 |
| `binary` | 17,354 |
| `compound` | 10,000 |

`operator` is **only ever `any` or `all`** — no other value exists anywhere in the corpus (the extractor specifically captured "any other operator value" and found none). This is the definitive answer to the headline question.

**Scope note:** the rich `condition` block appears in **307 of ~10,441 symphonies** (the vast majority use the flat single-condition form). But within those, compound logic is deeply nested (9,655 `any` + 667 `all` operator occurrences). So compound conditions are a real, in-use, but minority feature.

### 7.2 Leaf operand shape — VERIFIED-CORPUS

Inside `binary` / `binary-compound`, operands are objects:

- **`lhs`**: always `{"fn":..., "ticker":..., "params":{"window":N}}` (n=37,774 — `fn`/`ticker`/`params` all present 100%).
- **`rhs`**: two forms —
  - `{"constant": N}` — fixed-value comparison (n=35,130, dominant).
  - `{"fn":..., "ticker":..., "params":{"window":N}}` — indicator/ticker comparison (n=2,644).
- **`params`** inside operands: only `window` (n=39,853). (Note: this differs from the flat if-child form, which can also have `short-window` etc.; compound operands observed only `window`.)
- `comparator` inside compound leaves (census across compound blocks): `gt` (21,189), `lt` (16,578), `gte` (5), `lte` (2).

### 7.3 Real ANY-gate (OR) example — VERIFIED-CORPUS

Tickers are public symbols (no PII). A top-level `operator:"any"` joining one `binary` and four `binary-compound` predicates:

```json
{
  "condition-type": "compound",
  "operator": "any",
  "conditions": [
    {"condition-type": "binary",
     "lhs": {"fn": "relative-strength-index", "ticker": "EQUITIES::XLY//USD", "params": {"window": 10}},
     "comparator": "gt",
     "rhs": {"constant": 80}},
    {"condition-type": "binary-compound", "operator": "any", "tickers": ["FDL"],
     "lhs": {"fn": "relative-strength-index", "ticker": "%", "params": {"window": 10}},
     "comparator": "gt", "rhs": {"constant": 83}},
    {"condition-type": "binary-compound", "operator": "any", "tickers": ["IDLV"],
     "lhs": {"fn": "relative-strength-index", "ticker": "%", "params": {"window": 10}},
     "comparator": "gt", "rhs": {"constant": 80}},
    {"condition-type": "binary-compound", "operator": "any", "tickers": ["RETL"],
     "lhs": {"fn": "relative-strength-index", "ticker": "%", "params": {"window": 10}},
     "comparator": "gt", "rhs": {"constant": 86}},
    {"condition-type": "binary-compound", "operator": "any", "tickers": ["XLE"],
     "lhs": {"fn": "relative-strength-index", "ticker": "%", "params": {"window": 10}},
     "comparator": "gt", "rhs": {"constant": 89}}
  ]
}
```
Reads: "fire the true-branch if ANY of: RSI10(XLY)>80, RSI10(FDL)>83, RSI10(IDLV)>80, RSI10(RETL)>86, RSI10(XLE)>89."

### 7.4 Real ALL-gate (AND) example, with both rhs forms — VERIFIED-CORPUS

A top-level `operator:"all"` whose leaves show BOTH `rhs:{constant}` and `rhs:{fn,ticker,params}` (ticker comparison):

```json
{
  "condition-type": "compound",
  "operator": "all",
  "conditions": [
    {"condition-type": "binary-compound", "operator": "any", "tickers": ["SPY"],
     "lhs": {"fn": "moving-average-return", "ticker": "%", "params": {"window": 400}},
     "comparator": "lt",
     "rhs": {"fn": "moving-average-return", "ticker": "DBC", "params": {"window": 360}}},
    {"condition-type": "binary-compound", "operator": "any", "tickers": ["VIXM"],
     "lhs": {"fn": "relative-strength-index", "ticker": "%", "params": {"window": 10}},
     "comparator": "lt", "rhs": {"constant": 73.5}},
    {"condition-type": "binary-compound", "operator": "any", "tickers": ["VTV"],
     "lhs": {"fn": "relative-strength-index", "ticker": "%", "params": {"window": 10}},
     "comparator": "gt", "rhs": {"constant": 30}}
  ]
}
```
Reads: "fire only if ALL of: MA-return400(SPY) < MA-return360(DBC), AND RSI10(VIXM) < 73.5, AND RSI10(VTV) > 30."

### 7.5 The flat/condition dual-encoding (critical for parsers)

A compound `if-child` carries BOTH a flat condition (the legacy `lhs-fn`/`comparator`/`rhs-val` keys — a degenerate mirror of the first/primary predicate) AND the authoritative `condition` block. Observed `if-child` keys on a compound node included: `lhs-fn, lhs-fn-params, lhs-val, comparator, rhs-fn, rhs-fn-params, rhs-val, rhs-fixed-value?, condition, ...`. **The `condition` block is authoritative for compound logic; the flat keys are a lossy summary.** `symphony_logic._scan_condition` already recurses `conditions[]` for indicator extraction (but ignores `operator` — see §10).

`condition` appears on n=11,838 if-child nodes (matches the §7.1 `condition-type` block count).

---

## 8. Comparators — VERIFIED-CORPUS (resolves v1 OQ-2)

Full census of flat `if-child` `comparator` values:

| Comparator | n | v1 status |
|-----------|---|-----------|
| `gt` | 1,326,289 | VERIFIED-LOCAL |
| `lt` | 565,169 | VERIFIED-LOCAL |
| **`gte`** | **39,596** | **OPEN in v1 — CONFIRMED real.** Resolves OQ-2. |
| **`lte`** | **34,717** | v1 VERIFIED-COMMUNITY only — now VERIFIED-CORPUS |

**Resolution of OQ-2:** the comparator enum is **`{gt, lt, gte, lte}`** (4 values). **`eq` and `neq` do NOT exist** in the corpus (0 occurrences) — drop them as candidates. **Recommendation:** extend `symphony_schema.KNOWN_COMPARATORS` from `{gt, lt, lte}` to add **`gte`**.

(Note: the `builder_backtests` collection uses literal symbol comparators like `">"` — that is a different, non-Composer schema; see §10.)

---

## 9. Node `id` Format — VERIFIED-CORPUS

- **Child/internal node ids are UUID v4** strings (e.g. `e4d50619-927a-4214-8f23-03aff8f254b4`). VERIFIED-CORPUS.
- **The ROOT `id` is frequently NOT a UUID** — it is a short opaque token (e.g. `WFPLs3ulQOZK9c2FyumJ`, `kbFswCJKzLkCvfSSmiTI` — 20-char Composer symphony handles / Firebase-style push ids), reflecting the saved-symphony id rather than a generated node id. So v1 §9.1's "all ids are UUID v4" is **partially refuted at the root**: internal nodes are UUIDs, the root id is the symphony's external handle.
- **Construction implication:** keep generating UUID v4 for all constructed nodes (`symphony_schema._fresh_id`); the root id for a synthetic backtest tree can be any string (the backtest client reads `raw_value.id`). OQ-3 (are ids required by POST /backtest) remains backend-OPEN — corpus can't answer.

---

## 10. Corpus-vs-v1 Delta (what v1 missed / got wrong)

**Newly discovered in corpus, absent or wrong in v1:**

1. **`operator` key** — the actual ANY/ALL selector. v1 §7/§16.6 noted `operator:"any"` exists but never pinned that `operator` (with values `any`/`all`) IS the AND/OR distinction, and never showed an `all` example. **Now fully resolved.**
2. **`condition-type:"binary-compound"` + `tickers[]`** — the expression-level ANY/ALL broadcast (e.g. RSI of ANY(list)). v1 only mentioned `binary`/`compound`/`binary-compound` as tolerated strings; the `tickers[]` broadcast semantics + `ticker:"%"` placeholder were undocumented.
3. **`rhs:{"constant":N}`** — the fixed-value form inside compound blocks (vs flat `rhs-val`/`rhs-fixed-value?`). v1 did not document the `{constant:}` wrapper.
4. **`weight-method`** field (n=572 on group; values `percentage`/`fraction`) — a previously-undocumented specified-weight sub-mode. (NOT market-cap, contrary to a first guess — market-cap is unobserved; see §2.)
5. **`exponential-moving-average-price`** (45,816), **`standard-deviation-price`** (5,572), **`percentage-price-oscillator`** / **`-signal`**, **`upper-bollinger`** / **`lower-bollinger`** — real indicator fns v1 marked UNVERIFIED or omitted.
6. **`short-window`/`long-window`/`smooth-window`/`standard-deviations`** indicator params (for PPO / Bollinger) — v1 claimed `window` was the sole param.
7. **`rebalance:"quarterly"` and `"yearly"`** — beyond v1's 4.
8. **`select?` / `sort-by?`** booleans on filter nodes; **`window-days`** as a flat param on `wt-inverse-vol` (100% of them) and on weight containers.
9. **Root always has `description`, often `asset_class`/`asset_classes`** — v1 treated these as cosmetic extras / UNVERIFIED.
10. **`builder_backtests` collection uses a DIFFERENT, non-Composer schema** — `{nodeType, trueBranch, falseBranch, condition:{type:"simple", left:{type:"rsi", ticker, window}, right:{type:"fixed-value", value}, comparator:">"}}`. **Fixture-provenance warning:** do NOT use `builder_backtests.edn_string` as a Composer-grammar fixture — it is an internal builder representation with symbol comparators (`">"`) and `type:"rsi"` indicators, not the symphony `raw_value` form. Only `strategies.edn_string` is the real Composer symphony format. (`builder_backtests` had 0 `condition-type` but 204 `operator` — that `operator` belongs to the different schema.)

**v1 claims refuted by corpus:**
- v1 §4.2 / OQ-12 refutation of `standard-deviation-return` as a sort-by-fn is **wrong** — it has n=65,197 in sort position (§4b).
- v1 §9.1 "all node ids are UUID v4" is wrong at the root (§9).

**v1 claims confirmed by corpus:** the 9-step `KNOWN_STEPS` is complete; the flat if-child dual key-sets (with/without `rhs-fixed-value?`, with/without `rhs-fn`); flat `lhs-window-days`/`rhs-window-days` coexist with `*-fn-params` (n=608,487 / 299,210); `select-n` as string; weight on many node types; cosmetic-key tolerance.

---

## 11. `symphony_schema.py` Constructor-Gap Findings

Read against `advisors/symphony_schema.py` (constructors `make_if`, `make_condition`, `make_indicator`, `make_filter`, `make_group`). Each gap is the real-corpus pattern the constructors cannot represent. **Reported only — no code edited.**

- **GAP-1 (CRITICAL) — No compound ANY/ALL gate.** `make_if(condition, ...)` (lines 772-812) extracts a SINGLE flat condition into `lhs-fn`/`comparator`/`rhs-*` on one if-child. `make_condition` (704-762) returns one `{lhs, comparator, rhs}` descriptor. **Neither can emit a `condition` block with `operator:"any"`/`"all"` and a `conditions:[]` array.** Any "if ALL of X,Y,Z" / "if ANY of X,Y,Z" objective is inexpressible. This is the single most important gap — it blocks the operator's explicitly-requested ANY/ALL feature. (n=9,655 `any` + 667 `all` in the corpus prove this is a real, used pattern.)
- **GAP-2 — No `binary-compound` ticker-broadcast.** The "RSI of ANY(TQQQ, VTV, XLF) > X" form (`condition-type:"binary-compound"`, `tickers:[...]`, `ticker:"%"` placeholder) cannot be built — `make_indicator` (690-701) takes exactly one `ticker` and emits a single operand. No constructor accepts a ticker LIST.
- **GAP-3 — Comparator allowlist too narrow.** `KNOWN_COMPARATORS = {gt, lt, lte}` (line 80) **rejects `gte`** as a hard error, but `gte` is VERIFIED-CORPUS (n=39,596). `validate_tree` would hard-error on a real `gte` if-child. Fix: add `gte`.
- **GAP-4 — Rebalance allowlist too narrow.** `KNOWN_REBALANCE = {daily, none, weekly, monthly}` (line 84) **rejects `quarterly` and `yearly`** (both VERIFIED-CORPUS), making `validate_tree` hard-error on real symphonies that use them.
- **GAP-5 — Indicator allowlist incomplete (lint only, lower severity).** `KNOWN_INDICATOR_FNS` (7 fns, lines 65-75) omits `exponential-moving-average-price` (45,816), `standard-deviation-price` (5,572), PPO/PPO-signal, Bollinger. These only trigger `lint_tree` warnings (not hard errors), so trees still validate — but the lint noise is wrong: these are real, common Composer indicators. Fix: add at least EMA-price and std-dev-price.
- **GAP-6 — No market-cap weighting constructor.** No constructor emits a market-cap weighting. (Lower priority — market-cap is unobserved in the corpus; its serialization is OPEN.) No constructor emits the `weight-method` (percentage/fraction) attribute either.
- **GAP-7 — Indicator params fixed to `window`.** `make_indicator`/`make_filter` only emit `{"window": N}`. The PPO (`short-window`/`long-window`/`smooth-window`) and Bollinger (`standard-deviations`) params cannot be expressed. (Low priority — PPO/Bollinger are rare: ~200 / ~2 occurrences.)
- **NON-GAP (positive finding):** `validate_tree`'s compound-tolerance (lines 327-328: a true-branch if-child with a `condition` dict is exempt from flat-field requirements) is **correct** and matches the corpus dual-encoding. `_scan_condition_fns` (428-449) correctly recurses `conditions[]` and reads `lhs.fn`/`rhs.fn`. So the **reader** handles compound blocks; only the **constructors** cannot build them.

---

## 12. Reconciliation of v1 OPEN Questions

| v1 OQ | Question | Resolution |
|-------|----------|------------|
| OQ-1 | Does `wt-market-cap` step exist? | **RESOLVED — NO** (0/10,441). Market-cap weighting unobserved entirely; its encoding is OPEN. §2/§3. |
| OQ-2 | Does `gte` exist as a comparator? | **RESOLVED — YES** (n=39,596). Enum = {gt, lt, gte, lte}. `eq`/`neq` do NOT exist. §8. |
| OQ-3 | Are node `id`s required for POST /backtest? | **STILL OPEN** — backend behavior; corpus can't answer. Keep emitting UUIDs. |
| OQ-4 | Must `wt-cash-specified` weights sum to 100? | **RESOLVED — no hard gate.** Fractional `num` + weight on mixed node types make a strict 100-sum invalid; advisory lint only. §3. |
| OQ-5 | Rebalance values beyond daily/weekly/monthly/none? | **RESOLVED — `quarterly` (58) + `yearly` (27) also exist;** threshold = `none` + `rebalance-corridor-width`. §6. |
| OQ-6 | What does backtest_version v1 vs v2 change? | **STILL OPEN** — backend behavior; corpus can't answer. |
| OQ-7 | POST /backtest payload size limit? | **INFORMED** — corpus has symphonies far over 500 nodes (largest are 150KB+ JSON, thousands of nodes); 500-node construction bound is conservative, the API clearly accepts much larger. |
| OQ-8 | Does `wt-inverse-vol` take params? | **RESOLVED — YES, `window-days`** (flat int, on 100% of inverse-vol nodes). §3. |
| OQ-9 | Does `exponential-moving-average-price` work? | **RESOLVED — YES** (n=45,816, the 8th-most-common indicator). §4. |
| OQ-10 | What does `rebalance-corridor-width` control? | **RESOLVED** — threshold/corridor rebalance drift band; pairs 1:1 with `rebalance:"none"` (n=2,593). VERIFIED-DOC + CORPUS. §1/§6. |
| OQ-11 | Does GET /score work for non-owned symphonies? | **STILL OPEN** — backend auth behavior; corpus can't answer. |
| OQ-12 | `cumulative-return` / `standard-deviation-return` as sort-by-fn? | **RE-RESOLVED — BOTH valid** (cum-return 108,028; **std-dev-return 65,197** — v1's refutation was a 2-fixture artifact). §4b. |

**Tally: 9 of 11 actionable OQs resolved** (OQ-1,2,4,5,8,9,10,12 fully; OQ-7 informed). 3 remain genuinely OPEN as backend behaviors (OQ-3, OQ-6, OQ-11) that no corpus of saved symphonies can answer.

---

## 13. Sources

| Source | Tier | Date | Notes |
|--------|------|------|-------|
| MongoDB `captplanet.strategies` (`edn_string`, 10,441 parsed of 11,164) | VERIFIED-CORPUS | 2026-06-14 (mined) | Ground-truth real Composer symphonies (JSON-serialized). All §1–§9 frequency counts. |
| MongoDB `captplanet.builder_backtests` (`edn_string`, 709) | VERIFIED-CORPUS | 2026-06-14 | Different (non-Composer) builder schema — see §10 #10. |
| help.composer.trade/article/54 (Create a Symphony) | VERIFIED-DOC | 2026-06-14 | 4 weight schemes (incl. Market-cap); ANY/ALL; if/else; filter; group. |
| help.composer.trade/article/76 (Threshold Trading) | VERIFIED-DOC | 2026-06-14 | Corridor/threshold rebalance = drift-band; "Trading Setting" in editor. |
| api.composer.trade/docs | VERIFIED-DOC | 2026-06-14 | `weight:{num,den}` as strings; `rebalance-corridor-width`; `collapsed?`/`suppress-incomplete-warnings?` flags. |
| Composer web search (ANY/ALL feature) | VERIFIED-DOC | 2026-06-14 | "ANY: if any condition true… ALL: if all true… nest them… ANY/ALL within expressions (RSI of ANY(TQQQ,VTV,XLF))". |
| `advisors/symphony_schema.py` | VERIFIED-LOCAL | 2026-06-14 (read) | Constructor-gap analysis §11. |
| `symphony_logic.py` (`_scan_condition`) | VERIFIED-LOCAL | 2026-06-14 (read) | Reader recurses `conditions[]`, ignores `operator`. |
| `feature-plans/strategy-builder-composer-grammar.md` (v1) | prior | 2026-06-11 | Reconciled throughout. |
