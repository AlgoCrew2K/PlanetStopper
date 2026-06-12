# Composer Symphony `raw_value` Decision-Tree Grammar Reference

**Author:** composer-api-researcher
**Date:** 2026-06-11
**Confidence Summary:** High confidence on core vocabulary (root/asset/if/if-child/filter/group/wt-cash-equal/wt-cash-specified confirmed from local fixtures). Medium confidence on extended step types (wt-inverse-vol confirmed local-large-fixture; wt-market-cap community only). Open questions clearly marked.

---

## Purpose

This document pins the node vocabulary for the `advisors/symphony_schema.py` module that CONSTRUCTS synthetic symphony trees for submission to `POST /api/v0.1/backtest`. Every claim distinguishes its evidence tier:

- **VERIFIED-LOCAL** — observed verbatim in repo fixtures or production code paths
- **VERIFIED-COMMUNITY** — corroborated by community-tier sources with traceable URLs
- **UNVERIFIED** — best-guess from EDN/documentation analysis; treat as runtime-tolerant validation point

---

## 1. Top-Level (`root` node) Shape

The `raw_value` tree is itself the root node. Required top-level fields observed in local fixtures:

| Field | Type | Example | Evidence |
|-------|------|---------|----------|
| `step` | string | `"root"` | VERIFIED-LOCAL (`sample_score_small.json`, `sample_score_large.json`) |
| `name` | string | `"My Symphony"` | VERIFIED-LOCAL (`symphony_logic.py` accesses `raw_score.get("name")`) |
| `rebalance` | string | `"daily"` | VERIFIED-LOCAL (fixture grep; see Section 6) |
| `children` | array | `[...]` | VERIFIED-LOCAL |
| `id` | string (UUID v4) | `"d74bd787-..."` | VERIFIED-LOCAL (backtest response `active_asset_nodes` keys are UUIDs) |

Optional top-level fields observed in prior research (2026-05-31 swagger):

| Field | Evidence |
|-------|----------|
| `asset_class` | UNVERIFIED — present in swagger schema; semantics undocumented |
| `asset_classes` | UNVERIFIED — present in swagger schema; semantics undocumented |
| `rebalance-corridor-width` | UNVERIFIED — present in swagger schema; valid values unknown |

---

## 2. `step` Vocabulary

### 2.1 Confirmed Step Values

| Step value | Description | Evidence |
|------------|-------------|----------|
| `"root"` | Tree root (top-level node) | VERIFIED-LOCAL |
| `"group"` | Named container; groups branches with a creator-authored label | VERIFIED-LOCAL |
| `"if"` | Conditional branch container; children are `if-child` nodes | VERIFIED-LOCAL |
| `"if-child"` | True or else branch of an `if`; carries the condition and its sub-tree | VERIFIED-LOCAL |
| `"filter"` | Selects top/bottom N assets by indicator; children are `asset` candidates | VERIFIED-LOCAL |
| `"wt-cash-equal"` | Equal-weight allocation node | VERIFIED-LOCAL |
| `"wt-cash-specified"` | Specified-weight allocation; children carry `weight` field | VERIFIED-LOCAL |
| `"wt-inverse-vol"` | Inverse-volatility weight | VERIFIED-LOCAL (`sample_score_large.json`) |
| `"asset"` | Leaf node holding a single ticker | VERIFIED-LOCAL |

### 2.2 Possible Additional Steps (OPEN)

| Step value | Source | Confidence |
|------------|--------|------------|
| `"wt-market-cap"` | Composer help docs (2026-05-31 scrape) list 4 weight types: Equal, Specified, Inverse-vol, Market-cap | VERIFIED-COMMUNITY — but NOT confirmed in any local fixture |

---

## 3. Per-Step Required and Optional Fields

### 3.1 `root`

```
{
  "step": "root",
  "name": <string>,
  "rebalance": <rebalance-value>,
  "id": <uuid-string>,
  "children": [<child-node>, ...]
}
```

### 3.2 `group`

```
{
  "step": "group",
  "name": <string>,          -- creator-authored label, required for group identity
  "id": <uuid-string>,
  "children": [<child-node>, ...]
}
```

`group` nodes act as visual/structural containers. They do not carry weight or condition fields themselves. VERIFIED-LOCAL.

### 3.3 `if`

```
{
  "step": "if",
  "id": <uuid-string>,
  "children": [<if-child>, <if-child>]  -- typically 2: true-branch + else-branch
}
```

`if` itself carries no condition; conditions live on `if-child` nodes. VERIFIED-LOCAL.

### 3.4 `if-child`

```
{
  "step": "if-child",
  "is-else-condition?": <boolean>,   -- false = true-branch, true = else-branch
  "lhs-fn": <indicator-fn>,
  "lhs-fn-params": {"window": <int>},
  "lhs-val": <ticker-string>,
  "comparator": <comparator-value>,
  "rhs-fixed-value?": <boolean>,     -- true = rhs-val is literal number string
  "rhs-val": <ticker-string-or-number-string>,
  "rhs-fn": <indicator-fn>,          -- OMIT when rhs-fixed-value? is true
  "rhs-fn-params": {"window": <int>},-- OMIT when rhs-fixed-value? is true
  "id": <uuid-string>,
  "children": [<child-node>, ...]
}
```

Notes:
- When `rhs-fixed-value?` is `true`, `rhs-val` is a numeric string (e.g., `"85"`, `"-0.15"`); `rhs-fn` and `rhs-fn-params` are absent. VERIFIED-LOCAL (empirically validated 2026-05-14 per prior research).
- When `rhs-fixed-value?` is `false`, `rhs-val` is a ticker string; `rhs-fn` and `rhs-fn-params` are present. VERIFIED-LOCAL (`rhs-fn` seen in fixtures).
- `lhs-fn-params` and `rhs-fn-params` always use `{"window": <int>}` as the sole parameter key — no other param keys observed in any local fixture. VERIFIED-LOCAL.
- Compound conditions via a nested `condition` block: see Section 7.

### 3.5 `filter`

```
{
  "step": "filter",
  "select-fn": <"top" | "bottom">,
  "select-n": <int-or-numeric-string>,   -- fixtures serialize as strings ("4", "2"); see §16.3 (handoff amendment 3)
  "sort-by-fn": <indicator-fn>,
  "sort-by-fn-params": {"window": <int>},
  "id": <uuid-string>,
  "children": [<asset-node>, ...]   -- candidate pool
}
```

Notes:
- `sort-by-fn-params` uses `{"window": <int>}` — exact key name confirmed VERIFIED-LOCAL (`sample_score_small.json`). The EDN source uses `:sort-by-window-days` but the JSON API serializes it as the nested `sort-by-fn-params.window` object. The flat `sort-by-window-days` key does NOT appear in local JSON fixtures (0 matches).
- `sort-by-fn` values observed in fixtures: `"moving-average-return"`, `"max-drawdown"`. VERIFIED-LOCAL (`sample_score_large.json`). Phase-2 templates T6/T7 additionally use `"cumulative-return"` and `"standard-deviation-return"` as `sort-by-fn` values per contract mandate — these appear in §4.1 as VERIFIED-LOCAL indicator fns but are **NOT confirmed as `sort-by-fn` values** in any local fixture (see §4.2 and OQ-12).
- `select-fn` values observed: `"top"`, `"bottom"`. VERIFIED-LOCAL (`sample_score_small.json`).

### 3.6 `wt-cash-equal`

```
{
  "step": "wt-cash-equal",
  "id": <uuid-string>,
  "children": [<child-node>, ...]
}
```

No weight field on the node itself; weight is computed equally across children. VERIFIED-LOCAL.

### 3.7 `wt-cash-specified`

```
{
  "step": "wt-cash-specified",
  "id": <uuid-string>,
  "children": [<child-node>, ...]   -- each child carries a "weight" field
}
```

Children of `wt-cash-specified` each have a `weight` field; see Section 5. VERIFIED-LOCAL.

### 3.8 `wt-inverse-vol`

```
{
  "step": "wt-inverse-vol",
  "id": <uuid-string>,
  "children": [<child-node>, ...]
}
```

VERIFIED-LOCAL (`sample_score_large.json`). No additional params observed. The volatility window is likely implicit or fixed by Composer.

### 3.9 `asset`

```
{
  "step": "asset",
  "ticker": <string>,           -- e.g. "SPY", "UVXY"
  "name": <string>,             -- human-readable name, can be empty string
  "exchange": <string>,         -- e.g. "BATS", "NYSE", "NASDAQ"
  "id": <uuid-string>
}
```

No `children` field. VERIFIED-LOCAL.

---

## 4. Indicator Function Vocabulary

### 4.1 Confirmed `lhs-fn` / `rhs-fn` Values

| Function string | Used as | Evidence |
|----------------|---------|----------|
| `"relative-strength-index"` | lhs-fn, rhs-fn | VERIFIED-LOCAL (both fixtures, dominant) |
| `"cumulative-return"` | lhs-fn | VERIFIED-LOCAL (`sample_score_small.json`, `sample_score_large.json`) |
| `"max-drawdown"` | lhs-fn, sort-by-fn | VERIFIED-LOCAL (`sample_score_large.json`) |
| `"current-price"` | lhs-fn | VERIFIED-LOCAL (`sample_score_large.json`) |
| `"standard-deviation-return"` | rhs-fn | VERIFIED-LOCAL (`sample_score_large.json`) |
| `"moving-average-price"` | rhs-fn | VERIFIED-LOCAL (`sample_score_large.json`) |
| `"moving-average-return"` | lhs-fn (via prior research), sort-by-fn | VERIFIED-LOCAL (`sample_score_small.json` sort-by; prior research 2026-05-31 as lhs) |

### 4.2 Observed `sort-by-fn` Values

| Function string | Evidence |
|----------------|----------|
| `"moving-average-return"` | VERIFIED-LOCAL (`sample_score_small.json`, `sample_score_large.json`) |
| `"max-drawdown"` | VERIFIED-LOCAL (`sample_score_large.json`) |
| `"cumulative-return"` | UNVERIFIED as sort-by-fn. Confirmed VERIFIED-LOCAL as `lhs-fn` in §4.1. Used in Phase-2 T6 (`momentum_top_n`) per contract mandate (§3 T6 table). Not observed as `sort-by-fn` in any local fixture. Requires `composer-api-researcher` confirmation before production use. See OQ-12. |
| `"standard-deviation-return"` | UNVERIFIED as sort-by-fn. Confirmed VERIFIED-LOCAL as `rhs-fn` in §4.1. Used in Phase-2 T7 (`low_vol_floor`) per contract mandate (§3 T7 table). Not observed as `sort-by-fn` in any local fixture. Requires `composer-api-researcher` confirmation before production use. See OQ-12. |

### 4.3 Possible Additional Indicators (OPEN)

The following appeared in prior research (2026-05-31 swagger + Composer UI observation) but were NOT observed in local JSON fixtures:

| Function string | Source | Confidence |
|----------------|--------|------------|
| `"exponential-moving-average-price"` | Composer UI supports EMA | UNVERIFIED — exact API string not confirmed |
| `"standard-deviation-price"` | Analogous to `standard-deviation-return` | UNVERIFIED |

### 4.4 Parameter Encoding

All indicator functions that take a window use:
```json
{"window": <int>}
```
Observed window values: 3, 10, 20, 50, 100. No other parameter keys observed. VERIFIED-LOCAL (both `lhs-fn-params`, `rhs-fn-params`, and `sort-by-fn-params` in both fixtures).

The legacy EDN format used `:lhs-window-days` / `:rhs-window-days` / `:sort-by-window-days` as flat sibling keys. These do NOT appear in the JSON API representation — the JSON encoding uses the nested `{...-fn-params: {"window": N}}` pattern exclusively. VERIFIED-LOCAL (0 matches for `lhs-window-days` or `sort-by-window-days` in either fixture).

---

## 5. Weight Encoding

### 5.1 Shape

Weight is a rational fraction object:
```json
"weight": {"num": <number-or-string>, "den": <100-or-"100">}
```

`den` always represents `100`, but its JSON type is NOT always integer: the small fixture carries `den` as the string `"100"` as well as the integer `100`. `num` can be:
- An integer: `{"num": 20, "den": 100}` (= 20%)
- A numeric string: `{"num": "100", "den": 100}` or `{"num": "66.67", "den": 100}`

VERIFIED-LOCAL (`sample_score_large.json` — both integer and string `num`; `sample_score_small.json` — string `"100"` `den`). A validator must accept both `den` types; see §16.4 (handoff amendment 4).

### 5.2 Where Weight Appears

`weight` appears on nodes that are **direct children** of a `wt-cash-specified` node, and is also observed on `asset`/`if`/`group`/`filter` nodes outside that position (see §16.4 / handoff amendment 4). It does not carry semantics on children of `wt-cash-equal`/`wt-inverse-vol`. VERIFIED-LOCAL.

### 5.3 Sum Constraint (OPEN)

The sum constraint (do children's weights need to sum to exactly 100?) is not validated locally. The fixture shows `{"num": "66.67", ...}` alongside `{"num": 20, ...}` suggesting floating-point fractions are accepted. Whether Composer enforces a sum constraint at POST /backtest time is OPEN — no error fixture captured.

---

## 6. Rebalance Values

| Value | Evidence |
|-------|----------|
| `"daily"` | VERIFIED-LOCAL (both fixtures) |
| `"none"` | VERIFIED-COMMUNITY (prior research 2026-05-31 swagger enum) |
| `"weekly"` | VERIFIED-COMMUNITY (prior research 2026-05-31 swagger enum) |
| `"monthly"` | VERIFIED-COMMUNITY (prior research 2026-05-31 swagger enum) |

OPEN: Whether `"quarterly"` or threshold-based rebalance values exist is not confirmed in any source.

---

## 7. Compound Conditions

Some `if-child` nodes carry a nested `condition` block instead of (or in addition to) top-level `lhs-fn`/`rhs-fn` fields. This is used for AND/OR logic:

```json
{
  "step": "if-child",
  "is-else-condition?": false,
  "condition": {
    "conditions": [
      {
        "lhs": {"fn": <indicator-fn>, "fn-params": {"window": <int>}, "val": <ticker>},
        "comparator": <comparator-value>,
        "rhs": {"fn": <indicator-fn>, "fn-params": {"window": <int>}, "val": <ticker>}
      },
      ...
    ]
  },
  "id": <uuid-string>,
  "children": [...]
}
```

This structure is confirmed by `symphony_logic.py`'s `_scan_condition()` which accesses `cond["lhs"]["fn"]`, `cond["rhs"]["fn"]`, and recurses into `cond["conditions"]`. VERIFIED-LOCAL (code + test fixtures).

The `lhs.fn` and `rhs.fn` inside `condition.conditions` use the same indicator string vocabulary as flat `lhs-fn`/`rhs-fn`. VERIFIED-LOCAL (`_scan_condition` and test in `test_symphony_logic.py`).

---

## 8. Comparator Values

| Value | Evidence |
|-------|----------|
| `"gt"` | VERIFIED-LOCAL (both fixtures — dominant) |
| `"lt"` | VERIFIED-LOCAL (both fixtures) |
| `"lte"` | VERIFIED-COMMUNITY (androslee `compose_symphony_parser` repo) |
| `"gte"` | OPEN — NOT seen in any local fixture; NOT confirmed in community sources |
| `"eq"` | OPEN — not observed |

---

## 9. Node ID Format

### 9.1 Format

Node `id` values are standard UUID v4 strings (e.g., `"d74bd787-48e3-4b7e-95d9-e5a3d51c2f09"`). VERIFIED-LOCAL — backtest response `active_asset_nodes` keys (captured 2026-05-31) are UUIDs.

### 9.2 ID Requirements for POST /backtest (OPEN)

Whether node `id` fields are required by the POST /backtest endpoint is OPEN. Observations:
- The `active_asset_nodes` response map is keyed by node ID, suggesting Composer uses IDs internally to track which nodes are active.
- `advisors/asset_swap_engine.py` preserves original node IDs when constructing mutated trees — suggesting IDs must be stable and valid for round-trip backtests.
- Whether Composer validates ID uniqueness, UUID format, or presence is undocumented.

**Recommendation for `symphony_schema.py`:** Generate fresh UUID v4 strings for all constructed nodes via `uuid.uuid4()`. Do not omit `id` fields.

---

## 10. POST /backtest Payload Contract

From `advisors/composer_backtest_client.py` (VERIFIED-LOCAL):

```json
{
  "symphony": {
    "raw_value": <root-node-tree>
  },
  "capital": <float>,
  "apply_reg_fee": <bool>,
  "apply_taf_fee": <bool>,
  "slippage_percent": <float>,        -- default 0.005
  "broker": <broker-string>,          -- default "alpaca"
  "backtest_version": <"v1" | "v2">   -- default "v2"
}
```

Optional fields from prior swagger research (2026-05-31):
- `abbreviate_days` (bool)
- `apply_subscription` (bool)
- `spread_markup` (float)
- `start_date` (string, ISO date)
- `end_date` (string, ISO date)
- `benchmark_symphonies` (array)
- `benchmark_tickers` (array)
- `sparkgraph_color` (string)

Broker enum values: `"ALPACA_OAUTH"`, `"ALPACA_WHITE_LABEL"`, `"APEX_LEGACY"`, `"alpaca"`, `"apex"`.

The `symphony_id` is taken from `raw_value.get("id", "")` — the root node's `id` field. VERIFIED-LOCAL (`composer_backtest_client.py`).

---

## 11. Rate Limits

| Endpoint | Limit | Evidence |
|----------|-------|----------|
| POST /api/v0.1/backtest (inline) | 1 req/sec | VERIFIED-COMMUNITY (prior research 2026-05-31) |
| POST /api/v0.1/symphonies/{id}/backtest | 500 req/sec | VERIFIED-COMMUNITY (prior research 2026-05-31) |

**Construction implication:** For `symphony_schema.py`'s backtest loop, enforce a minimum 1-second delay between inline backtest calls.

---

## 12. GET /symphonies/{id}/score — Public Symphony Access

Whether `GET /symphonies/{id}/score` works for symphonies NOT owned by the caller is OPEN. No community confirmation found in either the Composer Discord public archives, Reddit/algo-db threads, or GitHub community repos. The endpoint requires standard Composer auth headers (`x-api-key-id`, `authorization`). Until confirmed, treat as "auth-user-owned symphonies only."

---

## 13. Open Questions (Runtime-Tolerant Validation Points)

| # | Question | Impact on `symphony_schema.py` |
|---|----------|--------------------------------|
| OQ-1 | Does `wt-market-cap` exist as a valid step value? | Skip for now; add if confirmed |
| OQ-2 | Does `"gte"` exist as a comparator? | Validate against `["gt", "lt", "lte"]` until confirmed |
| OQ-3 | Are node `id` fields required for POST /backtest? | Always include UUIDs; do not omit |
| OQ-4 | Does weight sum need to equal 100 for `wt-cash-specified`? | Sum weights; warn but do not error |
| OQ-5 | What are valid `rebalance` values beyond daily/weekly/monthly/none? | Restrict to confirmed 4 values |
| OQ-6 | What does `backtest_version` v1 vs v2 change? | Default to `"v2"` (project convention); no switching logic yet |
| OQ-7 | What is the payload size limit for POST /backtest? | Unknown; keep synthetic trees < 500 nodes as a conservative bound |
| OQ-8 | Does `wt-inverse-vol` accept any params (volatility window)? | No params observed; omit |
| OQ-9 | Does `"exponential-moving-average-price"` work as an indicator string? | UNVERIFIED; do not generate until confirmed |
| OQ-10 | What does `rebalance-corridor-width` control? | UNVERIFIED; omit from constructed trees |
| OQ-11 | Does GET /score work for non-owned public symphonies? | Assume user-owned only |
| OQ-12 | Are `"cumulative-return"` and `"standard-deviation-return"` valid `sort-by-fn` values for the `filter` step? Both are VERIFIED-LOCAL as indicator fns (`lhs-fn`/`rhs-fn`) but are NOT observed as `sort-by-fn` in any fixture. Phase-2 T6/T7 templates use them per contract mandate; `composer-api-researcher` confirmation needed before production use. | Use per Phase-2 contract but flag as UNVERIFIED in §4.2; obtain fixture confirmation |

---

## 14. Minimal Valid Tree Example

A minimal tree that should backtest successfully, based on VERIFIED-LOCAL patterns:

```json
{
  "step": "root",
  "name": "Synthetic Test",
  "rebalance": "daily",
  "id": "<uuid>",
  "children": [
    {
      "step": "wt-cash-equal",
      "id": "<uuid>",
      "children": [
        {
          "step": "asset",
          "ticker": "SPY",
          "name": "SPDR S&P 500 ETF",
          "exchange": "NYSE",
          "id": "<uuid>"
        }
      ]
    }
  ]
}
```

---

## 15. Sources

| Source | Tier | Date | Method | Notes |
|--------|------|------|--------|-------|
| `/home/user/PlanetStopper/tests/fixtures/symphony_logic/sample_score_small.json` | 1 (local fixture) | 2026-06-11 (read) | VERIFIED-LOCAL | Real /score response; "Corporate Chaos 5 ways"; contains `filter`, `if-child`, `wt-cash-equal` nodes |
| `/home/user/PlanetStopper/tests/fixtures/symphony_logic/sample_score_large.json` | 1 (local fixture) | 2026-06-11 (read) | VERIFIED-LOCAL | Larger real /score response; adds `wt-inverse-vol`, `wt-cash-specified`, `max-drawdown`/`current-price`/`moving-average-price`/`standard-deviation-return` indicators |
| `/home/user/PlanetStopper/tests/fixtures/composer/backtest_inline_v1.json` | 1 (local fixture) | 2026-06-11 (read) | VERIFIED-LOCAL | Real POST /backtest response; `active_asset_nodes` keys confirm UUID format |
| `/home/user/PlanetStopper/symphony_logic.py` | 1 (production code) | 2026-06-11 (read) | VERIFIED-LOCAL | Tree walker; confirms `step`, `ticker`, `children`, `lhs-fn`, `rhs-fn`, `condition`, `name`, `rebalance` keys |
| `/home/user/PlanetStopper/advisors/composer_backtest_client.py` | 1 (production code) | 2026-06-11 (read) | VERIFIED-LOCAL | POST /backtest payload shape; broker/version defaults; `data_warnings` normalization |
| `/home/user/PlanetStopper/advisors/asset_swap_engine.py` | 1 (production code) | 2026-06-11 (read) | VERIFIED-LOCAL | Confirms `ticker`/`children` traversal; ID preservation in mutated trees |
| `/home/user/PlanetStopper/advisors/logic_change_engine.py` | 1 (production code) | 2026-06-11 (read) | VERIFIED-LOCAL | Confirms `lhs-fn-params`/`rhs-fn-params` are numeric param holders |
| `/home/user/PlanetStopper/feature-plans/ai-advisor-composer-api-research.md` | 1 (prior research) | 2026-05-31 (produced) | VERIFIED-COMMUNITY | Composer swagger.json scrape; empirically validated if-child shape; broker enums; rate limits |
| `/home/user/PlanetStopper/tests/symphony_logic/test_symphony_logic.py` | 1 (test code) | 2026-06-11 (read) | VERIFIED-LOCAL | Confirms step vocabulary in Hypothesis strategy; compound condition structure |
| `https://github.com/androslee/compose_symphony_parser` | 3 (community) | ~2023 (repo age) | VERIFIED-COMMUNITY | EDN parser; confirms `lte` comparator; `:sort-by-window-days` EDN key (serialized differently in JSON) |

---

## 16. Phase-1 fixture-verification corrections

This section records the corrections that the Phase-1 `advisors/symphony_schema.py`
implementation made to Sections 1–15 after the module was driven against the two
real `/score` fixtures (`sample_score_small.json` — 866 nodes, depth 19;
`sample_score_large.json` — 8455 nodes, depth 230). Both fixtures must
`validate_tree() == []`; where a Section 1–15 claim or the original Phase-1 brief
would have rejected a real fixture, the rule was relaxed to a **lint warning** or
a **read tolerance** rather than a hard validation error.

These corrections correspond to amendments 1–7 of
`feature-plans/strategy-builder-phase1-handoff.md`. They are the stable in-doc
anchor for the §3.5 and §5.1/§5.2 forward-references. Each correction states what
the grammar/brief originally implied, what the fixtures revealed, and how
`symphony_schema.py` actually behaves.

The split is deliberate and load-bearing:

- **`validate_tree`** returns HARD errors only and never raises — reserved for
  structural defects that make a tree unsubmittable (unknown step, missing
  required field, malformed weight, duplicate id, `if` missing a branch,
  non-asset leaf, unknown comparator/rebalance).
- **`lint_tree`** returns advisory warnings — vocabulary drift and policy/size
  concerns that real, working symphonies legitimately exhibit.

### 16.1 Size and depth caps are lint-only (amendment 1)

`MAX_TOTAL_NODES` (500) and `MAX_TREE_DEPTH` (100) are **construction-side
constants and `lint_tree` warning thresholds, not `validate_tree` hard errors.**
Both golden fixtures exceed any sane construction cap (866 nodes / 8455 nodes;
depth 19 / depth 230), so gating validation on these would reject real trees.
A tree over either cap validates clean and only earns a lint warning. The OQ-7
"< 500 node" bound in §13 governs trees we CONSTRUCT, not trees we VALIDATE.

### 16.2 Unknown indicator fns are lint-only (amendment 2)

An indicator-fn string outside `KNOWN_INDICATOR_FNS` (the 7 VERIFIED-LOCAL fns in
§4.1) is a **lint warning, not a hard error.** `standard-deviation-price` appears
3 times in the large fixture (it is the §4.3 UNVERIFIED candidate, observed live
but not on the confirmed list); rejecting it would fail a real tree. So
`validate_tree` ignores indicator-fn *values* entirely, and `lint_tree` surfaces
any unverified fn (including the `rsi` abbreviation, whose canonical form is
`relative-strength-index`). Hard errors that REMAIN per amendment 2: unknown
step, structurally missing required fields, duplicate ids, malformed weight
objects, `if` missing branches, non-asset leaves, and None/garbage input.

### 16.3 `select-n` may be string or int (amendment 3)

§3.5 typed `select-n` as `<int>`. The large fixture carries `select-n` as a
string (e.g. `"3"`) in 183 positions. `validate_tree` requires the `select-n`
*field to be present* on a
`filter` node (its absence is a hard error — Composer has no count to select) but
tolerates either an int or a numeric string as the value. Constructors
(`make_filter`) emit the int form.

### 16.4 Weight `den` may be string `"100"`; weight appears on many node types (amendment 4)

§5.1 typed `den` as the integer `100`, and §5.2 stated weight appears only on
direct children of `wt-cash-specified`. The large fixture carries `weight`
objects on `asset` / `if` / `group` / `filter` nodes (not only
`wt-cash-specified` children), and `num` as a numeric string (`"66.67"`) in 234
positions alongside the int form. The string `den` form (`"100"`) is pinned by
handoff amendment 4 as a read tolerance: the large fixture uses int `den`
throughout (525 occurrences, 0 string), but the small fixture does carry one
string `den` (1 string / 9 int), and the tolerance is contract per the amendment
regardless. `validate_tree` therefore validates a weight object's *shape*
wherever it appears (a hard error only when `den` is missing or either
`num`/`den` is non-numeric — e.g. `num: "abc"`) and does not restrict weight to
`wt-cash-specified` children. Numeric strings (`"66.67"`, `"100"`) are accepted.
Constructors (`make_weight_specified`) emit the int `den: 100` form.

### 16.5 Flat `lhs-window-days` / `rhs-window-days` tolerated on read (amendment 5)

§4.4 stated the JSON API uses only the nested `{...-fn-params: {"window": N}}`
form. The large fixture in fact carries the legacy flat keys `lhs-window-days`
(186 occurrences) and `rhs-window-days` (120 occurrences) *alongside* the
params-object form on real `if-child` nodes. `validate_tree` tolerates the flat
keys without error. Constructors emit the params-object form ONLY — they never
write the flat keys.

### 16.6 Compound `condition` blocks tolerated (amendment 6)

§7 documents the nested `condition` block. Real blocks additionally carry
`condition-type` (`compound`/`binary`/`binary-compound`), `operator` (`any`),
`tickers` arrays, `%` placeholder tickers, and `rhs: {"constant": N}`.
`validate_tree` tolerates all of these without a hard error. Critically, a
true-branch `if-child` that carries a `condition` block is **exempt** from the
flat-field requirement: it is NOT required to also supply `lhs-fn`/`comparator`
(those live inside the block), so the compound case validates clean.

### 16.7 Cosmetic keys tolerated everywhere (amendment 7)

Real fixtures carry presentation/metadata keys that carry no grammar meaning:
`collapsed?`, `suppress_incomplete_warnings`, `window-days`, `description`,
`name` on non-`group` nodes, and `children-count` / `price` / `dollar_volume` /
`has_marketcap` on assets. `validate_tree` ignores any unrecognized key — it
checks for the presence/shape of *required* fields and never errors on extras.

### 16.8 Constructor note — ticker-comparison conditions (NOT one of amendments 1–7)

This subsection documents a constructor capability surfaced by the Phase-1 domain
review; it is amendment-9-adjacent, **not** one of the fixture-verification
corrections 1–7, and is kept separate so it is not cross-checked against the
handoff amendment list.

Per §3.4, when `rhs-fixed-value?` is `false` the comparison is a ticker-vs-ticker
predicate and `rhs-fn` / `rhs-fn-params` are required. To let `make_condition` /
`make_if` construct such conditions (the most common real pattern, e.g.
`RSI(LQD) gt RSI(XLV)`), `make_condition` accepts an optional keyword
`rhs_indicator=make_indicator(...)`:

- numeric `rhs` → fixed-value comparison (`rhs-fixed-value? = True`); supplying
  `rhs_indicator` here raises `ValueError`.
- string `rhs` (ticker) → ticker comparison (`rhs-fixed-value? = False`);
  `rhs_indicator` is REQUIRED. Omitting it raises `ValueError` rather than
  silently emitting an if-child that `validate_tree` would then reject.

`make_if` writes `rhs-fn` / `rhs-fn-params` onto the true-branch `if-child` from
that descriptor. Additionally, a whole-number numeric `rhs` is stringified
without a trailing `.0` (`0.0` → `"0"`, matching the fixture `rhs-val` form;
fractional floats keep their decimals, e.g. `5.5` → `"5.5"`).
