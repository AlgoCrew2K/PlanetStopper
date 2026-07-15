# advisors/frontrunner_detector

> Locates a symphony's incumbent frontrunner overlay(s) — leading RSI-overbought → VIX/hedge cascades — in a live Composer `/score` tree via a structural size-cliff signature.

**Source:** `advisors/frontrunner_detector.py`
**Last updated:** 2026-07-11 (Wave-1 backend, frreview-APPROVED, P2-1 iterative-traversal hardening landed at `26c1364`)

## Overview

A **frontrunner overlay** is a leading cascade of `RSI(core-ticker) gt threshold` `if`-nodes that, when triggered, fire a small VIX/hedge basket ahead of a symphony's core strategy logic. `frontrunner_detector.py` (feature-plans/frontrunner-builder.md AC-2) walks a Composer `raw_value` tree and finds every such cascade, distinguishing it from the tree's core logic via a **size cliff**: the fire (hedge) branch is a small minority of nodes (single-digit to low-double-digit) versus the sibling continuation branch, which grows toward thousands of nodes of core logic.

The detector is grounded in direct inspection of the operator's 11 real captured `/score` trees (feature plan §Summary) — every named constant in this module (thresholds, ratios, node caps) is calibrated against that corpus, not guessed. It is **fail-loud by design**: a tree with no qualifying if-nodes, or one with if-nodes but no small-branch VIX-family ticker anywhere, reports zero cascades with an explicit `skip_reason` — it never fabricates a boundary.

Off-execution-path. Advisory-only (read-only tree walk; no writes, no network). Never raises (D-1) — a malformed tree degrades to an empty `DetectionResult` with a reason.

## Detection Signature

A candidate `if` node qualifies as a genuine cascade rung when **all** of the following hold:

1. It has exactly two `if-child` entries (a condition branch + a continuation branch), identified primarily via `is-else-condition?: False`/`True`, with a fallback to "whichever child actually carries a condition" when that marker is absent.
2. The condition branch is a flat RSI-family condition (`lhs-fn` matches `"relative-strength-index"` or `"rsi"`, substring-tolerant) with `comparator == "gt"`.
3. The threshold is in the plausible RSI-overbought range (`_RSI_OVERBOUGHT_MIN=50.0` .. `_RSI_OVERBOUGHT_MAX=100.0`) — for a **root-level** scan only; a nested scale-in tier is exempt (see below).
4. The smaller of the two branches (by node count) contains at least one VIX-family ticker (`VIX_FAMILY_TICKERS`).
5. The size-cliff signature holds between the two branches — either a **ratio** check (`small_n / large_n <= _SIZE_CLIFF_MAX_RATIO=0.30`) or an **absolute** check (`small_n <= _SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES=40`); either qualifies.
6. (Root scan only) the condition is not **self-referential** — it does not watch a VIX-family instrument's own indicator (see `_is_self_referential_timing_gate`).

A cascade may recurse into its own fire branch to resolve **scale-in tiers** (a nested `if` firing a heavier hedge at a higher RSI threshold, e.g. RSI>80→VIX blend, RSI>82.5→heavier UVXY) — the whole tiered chain is reported as ONE `Cascade`, its `overlay_tree` spanning every tier.

**Exclusion — internal inverse-VIX timing sub-strategies (AC-2):** an if-node whose condition watches a VIX-family instrument's own momentum (e.g. an SVXY-timing gate keyed on SVXY's own RSI) is not a frontrunner cascade at the root level — it is internal hedge-allocation machinery, not a leading trigger, and is excluded from independent detection. The same self-reference is *legitimate* inside an already-confirmed fire branch (a scale-in tier watching the hedge instrument's own RSI to decide whether to escalate) — `_qualifies_as_cascade_rung`'s `is_nested_tier` flag distinguishes the two contexts.

## Named Constants (calibration)

| Name | Value | Basis |
|------|-------|-------|
| `VIX_FAMILY_TICKERS` | `{VIXY, VIXM, UVXY, UVIX, VXX, SVXY, SVIX}` | Grounding note: "fire baskets always contain >=1 VIX-family instrument but not always VIXY" |
| `_RSI_FN_SUBSTRINGS` | `("relative-strength-index", "rsi")` | Real trees use `"relative-strength-index"`; substring match tolerates naming drift |
| `_SIZE_CLIFF_MAX_RATIO` | `0.30` | Widest observed legit rung across the 11 real trees: ~0.29 (226-vs-779-node hedge sleeve); a near-balanced core RSI gate (~1.11 ratio) must NOT qualify even if a VIX ticker is buried inside it |
| `_SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES` | `40` | Widest observed legit fire basket: 33 nodes; smallest observed false positive: 253 nodes. 40 sits comfortably between |
| `_RSI_OVERBOUGHT_MIN` / `_MAX` | `50.0` / `100.0` | Grounding note: "RSI(ticker) gt ~77-82.5"; the band is wider than the observed range to avoid false-negatives on future symphonies, while excluding clearly non-overbought gates (e.g. "gt 31") |
| `_CORE_PLACEHOLDER_PREFIX` | `"CORE_ASSET_"` | Fixture-only marker; a defensive check flags an overlay that swallowed stubbed core content with no VIX ticker of its own |

## Public Types

### `Cascade` (dataclass)

One detected leading frontrunner cascade (possibly multi-tier).

| Field | Type | Description |
|-------|------|--------------|
| `overlay_tree` | `dict` | The compiled `if` subtree spanning every tier of this cascade — same node shape as the source tree, with the continuation branch replaced by a size-anchored placeholder stub (see below) |
| `rsi_thresholds` | `list[float]` | Every overbought threshold across all tiers |
| `vix_tickers` | `set[str]` | Every VIX-family ticker found in the fire basket(s) |
| `group_name` | `str \| None` | The enclosing parallel sub-strategy's group name, or `None` at the tree root |

### `DetectionResult` (dataclass)

Returned by `detect_frontrunner_cascades`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `cascades` | `list[Cascade]` | One `Cascade` per detected leading overlay (one per qualifying parallel sub-strategy). May be empty. |
| `skip_reason` | `str \| None` | Set whenever `cascades` is empty, explaining WHY. Never both empty AND `None` (D-1: always explicit). |

## API Reference

### `detect_frontrunner_cascades(tree: dict) -> DetectionResult`

The public entry point. Walks the whole tree (`_find_cascade_roots`), builds a compact overlay for each qualifying cascade root (`_build_cascade_overlay`), and returns the aggregate result.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `tree` | `dict` | The full symphony decision-tree dict (`raw_value` shape) |

**Returns:** `DetectionResult` — never raises (D-1); a malformed tree degrades to `cascades=[], skip_reason="invalid tree: not a dict"`, or `skip_reason="detector error: <type(exc).__name__>"` on any unexpected internal error (e.g. a pathologically-shaped tree the walk can't safely handle) — this top-level try/except is unchanged by the P2-1 iterative-traversal hardening below and remains a regression guard around it.

**Skip reasons observed:**
- `"no incumbent frontrunner cascade detected — ..."` — no if-node anywhere matched the RSI/VIX signature.
- `"candidate cascade roots were found but all failed validation"` — a defensive fallback if every found root's built overlay turned out corrupted (swallowed core placeholder content with no VIX ticker of its own).
- `"detector error: RecursionError"` — pre-P2-1 behavior on a pathologically deep tree; the underlying cause is now closed (see Traversal Style below), but the honest-degradation contract at this boundary is unchanged and still covers any future unexpected internal error.

**Example:**
```python
from advisors.frontrunner_detector import detect_frontrunner_cascades

result = detect_frontrunner_cascades(symphony_tree)
if result.cascades:
    for cascade in result.cascades:
        print(cascade.vix_tickers, cascade.rsi_thresholds, cascade.group_name)
else:
    print("no frontrunner detected:", result.skip_reason)
```

## Internal Mechanics

- **`_find_cascade_roots`** — finds every candidate cascade-root `if` node. Once a root qualifies, it does not recurse further into either branch for THIS scan (the cascade's own tiers are resolved separately by the overlay builder); it continues scanning into non-qualifying `if` nodes' children so a nested/sibling cascade belonging to a different parallel sub-strategy is still found.
- **`_build_cascade_overlay`** — reconstructs a COMPACT overlay for a cascade root: the fire branch is kept (recursively compacted, since it may itself contain a nested scale-in tier or internal hedge sub-gate), and the continuation (large/core) branch is replaced with a `_STUBBED_CORE_CONTINUATION` placeholder-leaf list. The stub's leaf count is deliberately anchored to be `>=` the fire branch's own node count (`original_continuation_n = max(_count_nodes(continuation_child), fire_node_count + 1)`) — collapsing it to near-zero would invert the size comparison downstream consumers rely on to identify "which branch is the fire branch."
- **`_is_internal_hedge_subgate`** — inside an already-confirmed fire branch, a non-RSI sub-gate (e.g. `cumulative-return(UVXY) lt 5.5` deciding de-escalation) is not itself a reportable cascade tier, but its own small side is still hedge content and must be compacted the same way as a genuine tier — otherwise whatever core content sits in *its* own large/else branch would leak through uncompacted.

### Traversal style (P2-1, landed `26c1364`)

`_count_nodes`, `_collect_tickers`, and `_find_cascade_roots` are **iterative** (explicit-stack), mirroring `symphony_schema.py`'s established `(node, ...)` stack pattern. This closes a real gap found at code review: the operator's real trees run 8,000+ nodes and can be deep, not just wide; `frtest`'s empirical probe (`2df4ca6`) confirmed all three raised `RecursionError`, uncaught, on a synthetic 3,000-deep tree. `_find_cascade_roots` carries `(node, group_name)` pairs on its stack and pushes children in **reversed** order so LIFO pop order matches the original left-to-right depth-first recursion exactly — cascade-detection order on multi-cascade real trees is unchanged, not just the count. `detect_frontrunner_cascades`'s own top-level try/except (its D-1 boundary) was **not** touched by this change and remains the honest-degradation regression guard around the whole walk. Verified behavior-preserving against the full `test_frontrunner_detector.py` suite (all 11 real fixtures) — zero regressions; `_build_cascade_overlay`/`_compact_if_node`/`_compact_subtree` (the overlay-construction recursion, operating only on the already-small compact cascade subtree, not the full symphony) were left recursive — not in scope for this hardening.

## Testing

- `tests/advisors/test_frontrunner_detector.py` — 10 tests, validated against the operator's 11 real captured `/score` trees as fixtures (not synthetic-only): delimits each leading RSI→VIX cascade + boundary, excludes internal inverse-VIX timing subtrees, fails loud on a synthetic ambiguous tree, recurses parallel sub-strategies.
- `tests/advisors/test_frontrunner_deep_tree_hardening.py` — 5 tests (P2-1): 3 genuinely-RED-until-fixed depth tests (each of `_count_nodes`/`_collect_tickers`/`_find_cascade_roots` survives a 3,000-deep synthetic tree with correct counts/tickers, not just no-crash) + 2 regression-guard tests (the public `detect_frontrunner_cascades` D-1 boundary and its honest skip-reason contract stay intact).

## Internal Dependencies

- `copy`, `dataclasses`, `logging` — stdlib only. No imports from `database`, `symphony_schema`, `alpha_bot_execution`, or any network/execution module — this module is a pure, side-effect-free tree walk.

## Consumers

- `advisors/frontrunner_builder.py` — `_run_build_for_symphony` calls `detect_frontrunner_cascades` on each live symphony's `/score` tree, and `_gather_atlas_frontrunner_patterns` reuses the same detector against Atlas community-corpus trees to extract patterns (structural detection only — never trusts incoming `oos_metrics`).
