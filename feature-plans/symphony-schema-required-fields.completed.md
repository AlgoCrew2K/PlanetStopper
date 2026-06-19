# symphony_schema required-fields fix — unblock Composer backtests

Status: ready
Owner: PM-authored (E2E-verification program, item #5 fix)
Branch: feat/symphony-schema-required-fields (off origin/main 26196e8)

## Summary
The live Composer `POST /api/v0.1/backtest` API now ENFORCES two required fields that
`advisors/symphony_schema.py` constructors omit, so every Strategy-Builder candidate tree
(and every asset-swap / logic-change inline backtest — same constructors) fails with
HTTP 400/422 BEFORE reaching the FDR gate → zero survivors ("never produced a survivor"
= SOLVED). Diagnosed empirically with a live verification matrix (`composer-encode-spike`):
the `raw_value` request wrapper is FINE; only the tree CONTENT is missing fields.

- **Defect 1 — `make_root` (`symphony_schema.py:784`) omits `"description"`.** Live malli
  now requires it on the root node (present as `""` in every real `/score`). Proof: T1
  equal_weight + `description=""` → **HTTP 200** (was 400).
- **Defect 2 — `make_inverse_vol` (`symphony_schema.py:823`) omits `"window-days"`.** Live
  API 422s "unknown-function-parameter" without it on a `wt-inverse-vol` node. Proof: T3
  inverse_vol + `description=""` + `window-days=30` → **HTTP 200** (was 422). API accepts
  any positive int; 30 = Composer UI default.

Fix = add both fields to the two shared constructors. NOT the client wrapper, NOT the
T1–T7 template builders, NOT any encoding change.

## Acceptance Criteria
- **AC-1:** `make_root(...)` output includes `"description"` (default `""`).
- **AC-2:** `make_inverse_vol(...)` output includes `"window-days"` (default `30`).
- **AC-3 (LIVE — the real gate):** every Strategy-Builder template tree (T1–T7 across all 3
  objectives) backtests against the LIVE Composer API with **HTTP 200** (was 400/422).
  Verified by an opt-in live test + the PM re-run of `propose_strategies`.
- **AC-4:** every existing test/fixture asserting `make_root`/`make_inverse_vol` output shape
  is updated for the ADDITIVE keys (no test left asserting the old shape) — stale-by-intent
  updates, never weakened assertions.
- **AC-5 (no schema breakage):** `validate_tree`/`lint_tree` still accept the trees (they
  tolerate unknown keys, amendment 7) — confirm with a test.
- **AC-6 (no regression / the payoff):** `propose_strategies` (all 3 objectives) now reaches
  the FDR gate (candidates backtest 200 → survivors OR a legitimate strict-gate rejection,
  NOT all-rejected-by-400); asset_swap_engine + logic_change_engine inline backtests also
  reach 200 (same constructors). PM-verified live.

## Architecture
- Two additive one-line changes in `advisors/symphony_schema.py`:
  - `make_root` return dict += `"description": ""`.
  - `make_inverse_vol` return dict += `"window-days": 30`.
- Defaults derive from the live API contract (the spike's live 200s + the
  `composer-trade-mcp` `Root.description`/`WeightInverseVol.window_days` models) — provenance
  captured-from-producer, NOT parser-co-designed.

## Edge Cases
- Other constructors NOT exercised live yet (T2/T4/T5/T7 families, `make_indicator`,
  `make_condition`, etc.) may ALSO omit a now-required field → the AC-3 live re-run will
  surface them; FOLD any additional missing-field fixes into THIS cycle (same root-cause
  class) rather than shipping a partial fix.
- `validate_tree`/`lint_tree` tolerate unknown keys → the new keys won't trip validation.
- Callers comparing constructor output to a stored fixture → update the fixture (additive).

## Security Considerations
- None. Pure schema-dict construction; no I/O, no credentials in the change. The opt-in live
  test reads the Composer key from `.env` (same as existing live tests).

## Testing Strategy
- **Unit (quant-test-writer, RED-first):** assert `make_root` emits `description=""` and
  `make_inverse_vol` emits `window-days=30`; assert `validate_tree`/`lint_tree` still accept
  the resulting trees; UPDATE any existing constructor-shape tests/fixtures for the additive
  keys (stale-by-intent). Do NOT hardcode producer-computed values — assert the FIELD presence
  + default, not a backtest stat.
- **Opt-in LIVE test (`-m live` / `--include-live`):** build each T1–T7 template tree and POST
  `/backtest`; assert HTTP 200. Captured-from-producer reference: the spike's live matrix +
  `tests/fixtures/composer/backtest_inline_transit_v1.json`.
- **PM re-verify (the gate, PM-run):** re-run `propose_strategies` (3 objectives) live → all
  candidates backtest 200 → the FDR gate runs → survivors or legit strict-reject; spot-check
  asset_swap/logic_change inline backtests reach 200.

## Scope Boundaries
- **IN:** the two `symphony_schema.py` fields; affected fixture/test updates; any ADDITIONAL
  missing-required-field discovered by the AC-3 live re-run (same class); doc updates (the
  stale grammar-doc OQ-8 "no params, omit" note on `wt-inverse-vol`; the `symphony_schema`
  CLAUDE.md row; `sample_score_large.json` is stale on `wt-inverse-vol` — flag/refresh if a
  test asserts its shape).
- **OUT:** `composer_backtest_client.py` request wrapper (`raw_value` is CORRECT — do not
  switch to encoding_type/encoded_value, which 422s); the T1–T7 builders in
  `strategy_builder_engine.py` (they consume the constructors); the 3 new response keys
  (`rebalance_days`/`active_asset_nodes`/`tdvm_weights` — safe-ignored by `_parse_response`,
  tracked-not-fix); response-parser changes (none needed).
