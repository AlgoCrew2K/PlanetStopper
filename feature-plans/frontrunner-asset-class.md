# Feature: Frontrunner Draft Asset-Class Propagation
Status: ready
Created: 2026-08-25
DECISIONS key: DE-FR-ASSET-CLASS-001

## Summary
`advisors/composer_draft_client.py` hardcodes `_DEFAULT_ASSET_CLASS="EQUITIES"` (line 77) as the default for `save_symphony`'s `asset_class` param. The one production caller — `advisors/frontrunner_builder.py:approve_frontrunner_proposal` (the operator-approval → real-Composer-draft-create seam, `frontrunner_builder.py:2504-2511`) — passes `raw_value=candidate_tree` but OMITS `asset_class`, so every draft is stamped `EQUITIES` regardless of the incumbent symphony's real asset class. This is wrong for crypto/non-equity symphonies.

The real asset class is already available on `candidate_tree`: the splice preserves the incumbent Composer score tree's top-level `asset_class` (string) and `asset_classes` (array) keys (`_replace_node_by_id` rebuilds the root as `{**node, "children": ...}`, `frontrunner_builder.py:1071`, and it survives the DB JSON round-trip). It is present on ~5 of 11 real captured trees and absent on ~6, so the fix MUST fall back to `EQUITIES` when absent. Canonical Composer enum: `EQUITIES | CRYPTO | OPTIONS` (CRYPTO is legacy/platform-uncertain but still schema-enumerated — we propagate it faithfully; live acceptance is the deferred operator-gated task-zero live-create's concern, not this cycle's).

**Design decision [PM-ASSUMED]:** derive the asset class at the CALL SITE (`approve_frontrunner_proposal`) from `candidate_tree` and pass it explicitly into `save_symphony`. Do NOT make `save_symphony` inspect `raw_value` — its deliberate "pass `raw_value` through UNCHANGED, never inspect it" transport contract (`composer_draft_client.py:129-132`) stays intact; the domain derivation lives in the builder, which understands symphonies. `_DEFAULT_ASSET_CLASS` stays as the param default (the fallback other/absent callers get).

## Acceptance Criteria
- **AC-1** — In `approve_frontrunner_proposal`, before the `save_symphony` call, derive `asset_class` from `candidate_tree` via a new pure helper and pass it explicitly as `save_symphony(..., asset_class=<derived>)`. When the tree carries a valid top-level `asset_class` string, that value is sent (e.g. a CRYPTO incumbent → `asset_class="CRYPTO"`, no longer silently EQUITIES).
- **AC-2** — Fallback to `EQUITIES` when the tree lacks a usable asset class: `asset_class` key absent/empty/non-string AND no usable `asset_classes` array. (~6/11 real trees hit this — must send `EQUITIES`, never omit the field or send `None`.)
- **AC-3** — The derived value is validated against the canonical enum `{EQUITIES, CRYPTO, OPTIONS}` (case-exact per the Composer contract). An unrecognized value (garbage/typo/unknown) → `EQUITIES` fallback, never propagated raw. (Defensive: we never POST an out-of-enum asset_class.)
- **AC-4** — `asset_classes` (array) is a secondary source consulted ONLY when the top-level `asset_class` string is absent: if `asset_classes` is a non-empty list whose elements are homogeneous and in-enum, use that element; a mixed/ambiguous or out-of-enum array → EQUITIES fallback (never guess a single class from a mixed set). [PM-ASSUMED — the real fixtures are all homogeneous single-element, so this is defensive; the test-writer confirms against the real_tree fixtures.]
- **AC-5** — The derivation helper is exception-safe (D-1 never-raises): any malformed/None/non-dict `candidate_tree` or internal error degrades to `EQUITIES`, never raises out of `approve_frontrunner_proposal` (which is contractually D-1).
- **AC-6** — `save_symphony`'s transport contract is byte-preserved: it still never inspects `raw_value`; `_DEFAULT_ASSET_CLASS` and its signature default remain; the POST body shape is unchanged except that `asset_class` now carries the derived value on the frontrunner path. Zero change to detection/splice/gate/acceptance math.
- **AC-7** — The structural no-auto-trade boundary is preserved: no invest/deploy/trade symbol is introduced anywhere; `tests/security/test_frontrunner_no_trade_boundary.py` stays green.
- **AC-8** — All existing `test_composer_draft_client.py` tests stay green — in particular `test_save_symphony_asset_class_defaults_to_equities` (the param default is still EQUITIES when a caller omits it).

## Architecture
- **New pure helper** (in `advisors/frontrunner_builder.py`, e.g. `_resolve_draft_asset_class(candidate_tree: dict | None) -> str`): reads top-level `asset_class` (string), else `asset_classes` (homogeneous in-enum array element), validates against `{EQUITIES, CRYPTO, OPTIONS}`, returns `EQUITIES` on any absence/ambiguity/error. Module-level enum constant (e.g. `_COMPOSER_ASSET_CLASSES = ("EQUITIES", "CRYPTO", "OPTIONS")`) with a source comment citing `docs/research/composer/baseline__2026-05-12.md:86` (no magic strings scattered). Never raises.
- **Call-site change** (`frontrunner_builder.py:~2504-2511`): `asset_class = _resolve_draft_asset_class(candidate_tree)` then `save_symphony(..., asset_class=asset_class)`. One-line derivation + one added kwarg.
- **`composer_draft_client.py`: zero code change** (only its docstring may note that callers now pass a derived value — a doc touch, not logic). `_DEFAULT_ASSET_CLASS` stays.
- Advisory-only, off the 1-minute engine path. `alpha_bot_execution.py` / `math_engine.py` carry ZERO diff.

## Edge Cases
- `candidate_tree` is `None` / not a dict / empty → `EQUITIES`.
- `asset_class` present but not a string (int/list/None) → ignore, try `asset_classes`, else `EQUITIES`.
- `asset_class` present, a string, but not in the enum (e.g. `"equities"` lowercase, `"FOREX"`) → `EQUITIES` (case-exact enum; do not uppercase-coerce silently unless the plan explicitly decides to — default: exact match only, else fallback).
- `asset_classes` present but `asset_class` absent → use the array (homogeneous in-enum) per AC-4.
- `asset_classes` mixed (e.g. `["EQUITIES","CRYPTO"]`) → `EQUITIES` (never pick one arbitrarily).
- Both present but DISAGREEING (`asset_class="CRYPTO"`, `asset_classes=["EQUITIES"]`) → prefer the top-level string `asset_class` (it is the primary/canonical field per the Composer body spec).
- A CRYPTO incumbent → propagate `"CRYPTO"` faithfully even though the live API may reject it (that's the deferred live-create's problem; the display/contract intent is to send the incumbent's true class).

## Security Considerations
- No new external-input trust surface: `candidate_tree` is already trusted internal data (our own splice output, DB round-tripped); we only READ two keys off it.
- No credential/secret handling touched. No new network call (same single `save_symphony` POST).
- No-auto-trade boundary must not regress (AC-7) — the source-scan suite enforces it.
- D-1 never-raises must hold (AC-5) — a malformed tree must not crash the approval path.

## Testing Strategy
- **RED (adversarial, quant-test-writer):**
  - Unit tests on `_resolve_draft_asset_class`: valid `asset_class` string in-enum → returned; absent → EQUITIES; non-string → EQUITIES; out-of-enum string → EQUITIES; homogeneous in-enum `asset_classes` array (no string) → that element; mixed array → EQUITIES; disagreeing string-vs-array → string wins; None/non-dict/empty tree → EQUITIES; a genuine CRYPTO tree → "CRYPTO".
  - Real-fixture propagation: drive the helper over the 11 `tests/fixtures/advisors/frontrunner/real_tree_*.json` — assert the ~5 with `asset_class="EQUITIES"` return EQUITIES from the real value (not the hardcoded default) and the ~6 without return EQUITIES via fallback. (Use a synthetic CRYPTO-stamped copy of a real tree to prove non-EQUITIES propagation, since no crypto fixture exists.)
  - **Wiring test (mandatory — the gap the recon found):** in the approval-path harness (`test_frontrunner_approval.py` style, `save_symphony` mocked), assert the value that REACHES the mocked `save_symphony`'s `asset_class` kwarg equals the derived value — for both a candidate_tree carrying `asset_class="CRYPTO"` (→ mock receives "CRYPTO") and one lacking it (→ mock receives "EQUITIES"). This proves the derivation is actually THREADED into the real call, not just unit-correct in isolation.
  - Regression: `test_composer_draft_client.py::test_save_symphony_asset_class_defaults_to_equities` + the no-trade-boundary suite stay green.
- **GREEN (composer-alpaca-integration):** minimal helper + one-line call-site change; no gold-plating.
- **PM functional gate:** run the helper over the real droplet-captured trees + a synthetic CRYPTO case; confirm the derived asset_class matches the tree (real operand, not synthetic-only). Live Composer create stays deferred (operator-gated task-zero).

## Scope Boundaries
- **IN:** `advisors/frontrunner_builder.py` (new helper + call-site kwarg), its tests, docs (DECISIONS entry, generated docs, CLAUDE.md key-files row).
- **OUT:** `composer_draft_client.py` logic (docstring touch only); `save_symphony`'s transport contract; any live Composer create/verification (operator-gated task-zero); the strategy-builder retrofit path (funnels through the same `approve_frontrunner_proposal`, so it's covered for free — no separate change); detection/splice/gate/acceptance math; the 1-minute execution path.
- **NON-GOAL:** verifying Composer's live acceptance of CRYPTO/OPTIONS (deferred). We propagate faithfully; the live API contract is a separate operator-gated item.
