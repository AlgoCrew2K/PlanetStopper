# Composer Symphony Decision-Tree Logic Endpoint — Empirical Validation

**Author:** composer-alpaca-integration
**Date:** 2026-05-14
**Purpose:** Gate the A/C decision for the "Claude receives the symphony decision-tree logic as prompt context" feature. Confirm definitively which Composer endpoint returns the algorithmic strategy definition (not a stats rollup, not a score number).
**Method:** Read-only `GET` calls against the live Composer API using real credentials from the working-tree `.env`. No mutating calls made.

---

## VERDICT: **YES**

There is a Composer endpoint that returns a symphony's full decision-tree logic — the complete algorithmic strategy definition — in structured JSON suitable for feeding to Claude as prompt context.

The endpoint name "score" is misleading. It does **not** return a ranking or a numeric score. It returns the symphony's entire logic tree: nested conditionals, technical-indicator predicates, comparators, thresholds, asset weights, and tickers.

---

## Endpoint

| Property | Value |
|---|---|
| **Method + path** | `GET /api/v0.1/symphonies/{symphony-id}/score` |
| **Base URL** | `https://api.composer.trade/api/v0.1` (same base as existing AlphaBot Composer calls) |
| **Auth** | Identical to existing calls — `get_composer_headers()`: `x-api-key-id: <COMPOSER_KEY_ID>`, `authorization: Bearer <COMPOSER_SECRET>` |
| **Query params** | `score_version` ∈ {`v1`, `v2`}, default `v1`. Both return the logic tree (see Notes). |
| **Response** | `HTTP 200`, `application/json; charset=utf-8` |
| **Mutating?** | No — pure read. Safe for the live Roth IRA account. |

**Related read-only endpoints (also validated):**
- `GET /api/v0.1/symphonies/{symphony-id}/versions` → `HTTP 200`, list of `{version_id, created_at}` — version history.
- `GET /api/v0.1/symphonies/{symphony-id}/versions/{version-id}/score` → logic tree at a specific historical version (documented; not separately exercised this session, but `/versions` confirms the version IDs exist).

---

## Validation Procedure (executed)

1. `GET /api/v0.1/accounts/list` → `HTTP 200`. Account UUID `880be47e-efe4-4b44-9d83-b6d86098fe0d` confirmed (`account_type: ROTH_IRA`, `status: ACTIVE`, `has_active_position: true`).
2. `GET /api/v0.1/portfolio/accounts/{uuid}/symphony-stats-meta` → `HTTP 200`, 11 live symphonies returned. Extracted real symphony IDs (e.g., `hvPiGP1O7AHfutHE3Fjy` = "(INVEST) Planet of Hunted Cascades").
3. `GET /api/v0.1/symphonies/hvPiGP1O7AHfutHE3Fjy/score` → `HTTP 200`, **1,044,312-byte JSON body**. Parsed and walked the tree.
4. `GET .../score?score_version=v2` → `HTTP 200`, 958,881 bytes (same structural shape, see Notes).
5. `GET .../symphonies/{id}/versions` → `HTTP 200`, full version history returned.

---

## Actual Response Shape

**Top-level object keys:** `description`, `name`, `id`, `step` (`"root"`), `rebalance`, `asset_class`, `asset_classes`, `children`.

The logic lives in `children` — a recursively nested tree. Structural census of one real symphony ("Planet of Hunted Cascades"):

- **Max tree depth:** 230 levels
- **`step` node types:** `root` (1), `group` (697), `if` (1003), `if-child` (2006), `asset` (2973), `wt-cash-equal` (1554), `wt-cash-specified` (37), `wt-inverse-vol` (1), `filter` (183)
- **Technical-indicator functions used (`lhs-fn` / `rhs-fn`):** `relative-strength-index` (1066), `current-price` (72), `moving-average-price` (72), `max-drawdown` (60), `cumulative-return` (38), `standard-deviation-return` (18), `moving-average-return` (2)
- **Unique tickers referenced:** 92

This is the genuine algorithmic definition — every conditional branch, every indicator threshold, every weighting rule the symphony uses to allocate.

### Redacted sample (a real conditional branch, from a smaller symphony)

UUIDs shortened for readability; structure verbatim:

```json
{
  "step": "if-child",
  "is-else-condition?": false,
  "lhs-fn": "relative-strength-index",
  "lhs-fn-params": { "window": 10 },
  "lhs-val": "UVXY",
  "comparator": "lt",
  "rhs-fixed-value?": true,
  "rhs-val": "85",
  "id": "d74bd787-...",
  "children": [
    { "step": "asset", "ticker": "UVXY", "name": "ProShares Ultra VIX Short-Term Futures ETF", "exchange": "BATS", "id": "45cb7da2-..." },
    { "step": "asset", "ticker": "VIXY", "name": "ProShares VIX Short-Term Futures ETF", "exchange": "BATS", "id": "2fb1acfb-..." }
  ]
}
```

Read in plain English: *"IF the 10-day RSI of UVXY is less than 85, then hold UVXY and VIXY (equal-weight)."* Weighted nodes carry `weight: {num, den}` rationals (e.g., `{"num": 20, "den": 100}` = 20%). `group` nodes carry human-readable `name` labels (e.g., `"VIX Blend++"`, `"Scale-In | VIX+ -> VIX++"`) authored by the strategy creator — directly useful as semantic context for Claude.

---

## Notes / Caveats

1. **Payload size is large.** A single symphony's score tree ran ~1 MB of JSON (2,973 asset nodes, depth 230). Feeding raw score JSON to Claude verbatim will be token-expensive for complex symphonies. The feature will likely need a pre-processing/summarization pass (collapse `wt-cash-equal` wrappers, prune UUIDs/exchange metadata, flatten to a readable rule list) before it goes in a prompt. This is a feature-design consideration, **not** a blocker — the data is complete and structured.
2. **`score_version` v1 vs v2:** Both return `HTTP 200` with the same node vocabulary (`step`, `if`, `if-child`, `lhs-fn`, etc.). v2 was ~8% smaller for the test symphony — likely a serialization refinement, not a different contract. Open question from the baseline (Q4) remains: no documented behavioral diff. Recommend defaulting to `v1` (the documented default) until v2's differences are characterized; either works for the feature.
3. **`/versions` works** — enables the roadmap's historical-analysis item (logic tree as-of a past date) with no additional contract risk.
4. **Rate limit:** Standard 1 req/sec applies. One score call per symphony per analysis cycle is well within budget; this is not a minute-cadence execution-path call.
5. **Fixture provenance for the eventual parser:** Any parser built for this endpoint must use a **captured-from-producer** fixture (a real `/score` response saved via `/api-fixture`). Do NOT co-design the fixture with the parser — that is a Gate-1 fail. The response is now confirmed capturable.

---

## Recommendation

**The feature's "Claude receives the symphony logic" requirement CAN be met as written.** `GET /api/v0.1/symphonies/{symphony-id}/score` returns the complete decision-tree logic — conditionals, indicators, thresholds, weights, tickers, and creator-authored group labels — in structured JSON, using the auth pattern AlphaBot already implements.

Suggested A/C language: the feature consumes `GET /symphonies/{id}/score`, captures a real-response fixture first, and includes a logic-tree-to-prompt summarization step (the raw payload is too large to feed verbatim for complex symphonies). The "what the symphony does" context is fully available; the only engineering shaping needed is condensation, not contract work.
