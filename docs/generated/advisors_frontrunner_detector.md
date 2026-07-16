# advisors/frontrunner_detector

> Locates a symphony's incumbent frontrunner overlay(s) — leading RSI-overbought → VIX/hedge cascades — in a live Composer `/score` tree, and extracts every per-condition FR-check for signal classification. Direction-explicit (TRUE branch reaches a VIX-family ticker), never size-based.

**Source:** `advisors/frontrunner_detector.py`
**Last updated:** 2026-07-16 (AC-3/AC-6 rebuild, GREEN at `bf6f026b` — see `DE-FR-SIGNALS-001` in `DECISIONS.md`. Supersedes the 2026-07-11 wave-1 size-cliff signature described below, which is preserved as a dated historical section, not deleted — see "Superseded: the wave-1 size-cliff signature".)

## Overview

`frontrunner_detector.py` now has two public entry points doing related but distinct jobs, both operating on the same underlying direction rule:

- **`detect_frontrunner_cascades(tree)`** (feature-plans/frontrunner-builder.md AC-2, rebuilt this cycle per feature-plans/frontrunner-signals.md AC-6) — finds the leading cascade(s) a symphony already has, for the Frontrunner Builder's detect→generate→splice→gate→accept pipeline (`docs/generated/advisors_frontrunner_builder.md`).
- **`extract_fr_checks(tree)`** (feature-plans/frontrunner-signals.md AC-3, new this cycle) — walks the WHOLE tree and returns every individual FR-check condition, for joining against live Atlas signal data (`docs/generated/advisors_frontrunner_signals.md`).

**Both are now built on the identical canonical rule for QUALIFICATION: a condition node's TRUE branch reaching a VIX-family ticker.** This is a deliberate unification, not a coincidence — `_qualifies_as_cascade_rung` (the cascade-detection qualifying test) and `extract_fr_checks`'s own per-node test both resolve through the same `_get_condition_branch_pair` / `_collect_tickers` / `VIX_FAMILY_TICKERS` primitives. The wave-1 detector (2026-07-11, PR #96) used a DIFFERENT, size-based rule instead (the smaller of the two branches by node count); that rule is preserved below as a dated historical section because it is now a **named regression test anchor** (AC-6: "the old signature's 0-match behavior must never be the sole gate again"), not because it is still in use for qualification. **Important scope note (see Internal Mechanics below):** unification is for QUALIFICATION only — `detect_frontrunner_cascades`'s own overlay-construction step (deciding which of the two branches to label "fire" in the returned `Cascade.overlay_tree`) remains intentionally size-based, a deliberately different, narrower question from AC-3's walk.

Off-execution-path. Advisory-only (read-only tree walk; no writes, no network). Never raises (D-1) on either public entry point — a malformed tree degrades to an empty result with a reason (`detect_frontrunner_cascades`) or an empty list (`extract_fr_checks`).

## Detection Signature (canonical, AC-3/AC-6, `bf6f026b`)

A condition node qualifies — for cascade-rung purposes (`_qualifies_as_cascade_rung`) AND for FR-check extraction (`extract_fr_checks`) alike — when:

1. It has exactly two `if-child` entries, identified primarily via `is-else-condition?: False`/`True`, with a fallback (`_get_condition_branch_pair`) to "whichever child actually carries a condition" when that marker is absent.
2. **The condition's TRUE branch (the `is-else-condition?: False` child) reaches at least one VIX-family ticker anywhere in its subtree** (`_collect_tickers(cond_child) & VIX_FAMILY_TICKERS`) — this is the entire qualifying signal. Branch SIZE is never consulted for this question, in either direction.
3. (Cascade-rung qualification only, `_qualifies_as_cascade_rung`) the condition is additionally a flat RSI-family condition (`lhs-fn` matches `"relative-strength-index"`/`"rsi"`), `comparator == "gt"`, and has a parseable fixed numeric threshold (`_parse_rsi_threshold`, see the discriminator below). `extract_fr_checks` is deliberately fn-agnostic and comparator-agnostic at the walk level — filtering by fn/comparator is AC-4's join-time job (`classify_fr_checks` in `advisors/frontrunner_signals.py`), not this module's.
4. (Root-scan cascade-rung qualification only) the condition is not self-referential — it does not watch a VIX-family instrument's own indicator (`_is_self_referential_timing_gate`, subject-ticker test only — the old size-based extension of this test was dropped once branch size stopped being load-bearing for QUALIFICATION).

**Full traversal, not stop-on-match.** Both `_find_cascade_roots` and `extract_fr_checks` descend into BOTH branches of every `if`-node unconditionally (subject to the cascade-detector's own no-re-descend-into-a-confirmed-fire-branch rule, see AC-6 Bug Fixes below) — a qualifying node is never a stopping point. Real trees chain many independent FR gates as sibling if/elif-via-else ladders; a walk that stops at the first match misses everything downstream (this was the single largest defect in the wave-1 detector — see AC-6 Bug Fixes).

A cascade may still recurse into its own fire branch to resolve **scale-in tiers** (a nested `if` firing a heavier hedge at a higher RSI threshold) — the whole tiered chain is reported as ONE `Cascade`, its `overlay_tree` spanning every tier. This mechanic is unchanged from wave-1.

## The fixed-threshold vs. crossover discriminator (two-stage falsification — read this before trusting any RHS-shape claim elsewhere in the docs)

A flat condition's RHS can encode either a genuine fixed numeric threshold (`RSI(SPY,10) gt 31`) or a ticker-vs-ticker relative-strength comparison (`RSI(LQD,50) gt RSI(XLV,50)`). Distinguishing the two correctly matters for both AC-3 (which of `FRCheck.fr_key` or `FRCheck.rhs_ticker` gets populated) and AC-6 (whether `_parse_rsi_threshold` returns a real threshold or `None`).

**This rule was falsified once and corrected during this cycle — the full story, not a sanitized summary, because getting this wrong the first time is itself evidence for the cycle's core methodological point (tree-semantics rules must be producer-grounded, never agent-converged):**

1. **Stage 1 (falsified).** An initial analysis-layer hypothesis proposed: "a condition whose RHS carries an `rhs-fn` key is a crossover, never a fixed threshold." This produced an 8→5 correction to the operator's original genuine-`SPY:10:31` symphony count (dropping iaSO, Paragons-lW4Z, and n2oo as supposed crossover false positives) and a table of "21 contaminated fr_keys."
2. **Stage 2 (verdict, adversarial second pass, `.claude/fr-signals-inputs/mirror-pattern-verdict.md`, commit `93e27efb`).** A second, independent falsification pass (fr-falsifier2, evidence tags X1-X7) traced all three "reinstated" `SPY:10:31` nodes' TRUE branches directly and found them firing VIX-long — genuine cascades, not crossovers. The Stage-1 `rhs-fn`-presence rule was itself falsified: `rhs-fn` and `rhs-window-days` are **vestigial echoes from a non-canonical Composer export pathway**, present on both genuine fixed-threshold nodes and genuine crossovers alike, with zero correlation to which one a node actually is (exceptionless across ~1,900 real condition nodes swept).
3. **The corrected, exceptionless discriminator (`_parse_rsi_threshold` / `_is_ticker_comparison`, `bf6f026b`):** a node is a fixed-threshold check IFF `rhs-val` parses as a number (equivalently, `rhs-fixed-value?` is truthy — no nested rhs operand). A ticker `rhs-val` (`rhs-fixed-value?` explicitly `False`) is a genuine crossover. **`rhs-fn` is NEVER consulted for this question.** `_parse_rsi_threshold` already implemented this correctly before the Stage-1 hypothesis was ever proposed — the error was entirely in the intermediate analysis layer, not in the originally-shipped code.
4. **Final AC-6 expected-set (operator vindicated):** genuine VIX-firing `SPY:10:31` = **8 symphonies** (qF5Z/hvPi/INfC/Gpaw/MoAk/iaSO/lW4Z-Paragons/n2oo — the operator's original "8 of 11" claim, upheld). `5Xjz` (`real_tree_01`) carries the genuine RSI-gt-31 gate too but its TRUE branch routes to BTAL only, not VIX — a genuine-but-not-FR-check negative control, excluded for a structural reason (no VIX destination), never because it's a crossover. `n2oo`'s `SPY:21:30` is also a genuine fixed gate (TRUE→VIXM at depth-0), not the "Atlas-stats-only, crossover-contaminated" case an earlier draft of `feature-plans/frontrunner-signals.md`'s AC-4 fixture table briefly described. The "21 contaminated fr_keys" table from Stage 1 dissolves entirely — every one of those keys is a genuine fixed threshold.
5. **What genuinely IS real about `rhs-fn`:** for a genuine ticker-vs-ticker crossover, `rhs-fn` (and `rhs-fn-params.window`) DOES carry real, meaningful RHS-indicator data (verified against `real_tree_06` node `0d98c2bb`: `RSI(LQD,50) gt RSI(XLV,50)` — `rhs-fn="relative-strength-index"`, `rhs-fn-params.window=50`, matching the LHS window exactly). The SAME raw field means different things depending on context: vestigial noise on a fixed-threshold node, real data on a crossover node. `FRCheck`'s invariant reflects this: exactly one of `{fr_key, rhs_ticker}` is populated per check; `rhs_fn` MAY populate alongside `rhs_ticker` as optional enrichment, never alongside `fr_key`, and never load-bearing for joinability. `rhs_val` has no live population path under the corrected discriminator — the "crossover with a numeric RHS value" case was proposed, then proven not to exist in real data, and stays `None` in practice (kept on the dataclass only for shape stability).

**Practical consequence for every other doc in this tree that references the "8→5" correction or a "21 contaminated keys" table:** those are Stage-1 claims, now retracted. `DE-FR-SIGNALS-001` in `DECISIONS.md` carries the full retraction record with commit citations (`77060593` Stage 1, `93e27efb`/`07b4c0cb` the verdict + FRCheck amendment).

## Superseded: the wave-1 size-cliff signature (2026-07-11, PR #96 — historical, not in use)

The originally-shipped detector (wave-1, `docs/generated/advisors_frontrunner_builder.md`'s companion cycle) used a size-based signature instead of the direction-explicit rule above: a candidate `if` node qualified when the SMALLER of its two branches (by node count) contained a VIX-family ticker, subject to a ratio check (`small_n / large_n <= _SIZE_CLIFF_MAX_RATIO=0.30`) or an absolute check (`small_n <= _SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES=40`), plus a plausible-RSI-overbought-range floor (`_RSI_OVERBOUGHT_MIN=50.0` / `_RSI_OVERBOUGHT_MAX=100.0`).

**This signature is empirically falsified for QUALIFICATION and is preserved in the source ONLY as a named regression-test anchor (AC-6: "must never be the sole gate again"), not as a fallback or secondary check.** Two independent, measured defects, both fixed by the AC-3/AC-6 rebuild:

- **Direction inference was backwards on real trees.** The genuine `SPY:70:62` node in `real_tree_04_INfCn3eKsu6i4oTTqdUp.json` has its fire (VIX-reaching) branch on the LARGER side by node count — 51 nodes vs. the else branch's 43 (verified via `advisors.frontrunner_detector._count_nodes`, the exact function `_qualifies_as_cascade_rung`'s ratio/absolute checks call; both branches independently reach a VIX ticker, but the fire side is unambiguous only from direction). Under the old rule, this node fails BOTH sub-checks (43/51≈0.84 ratio exceeds 0.30; 43 nodes exceeds the 40-node absolute cap) — rejected regardless of the fact that it's a genuine cascade rung.
- **The overbought-range floor rejected real thresholds outright.** `SPY:10:31` (threshold=31.0) and `SPY:21:30` (threshold=30.0) both fail `_RSI_OVERBOUGHT_MIN=50.0` regardless of direction — the "overbought-only" assumption does not hold for the real signal population (the wider Atlas collection includes numerous sub-50 fixed-threshold checks).

Combined, these defects account for the shipped detector recovering **0 of 11 real trees' cascades** at the AC-2 wave-1 milestone and the severely low recovery rate measured against the AC-3 expected-set during this cycle's diagnosis phase (see `DE-FR-SIGNALS-001` for the exact, fr-engine-verified recovery number). `_SIZE_CLIFF_MAX_RATIO`, `_SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES`, `_RSI_OVERBOUGHT_MIN`, `_RSI_OVERBOUGHT_MAX` remain defined in the source (unused by the qualifying path — `_qualifies_as_cascade_rung` no longer references any of the four) purely to anchor the regression test proving the old signature's premise was mathematically blind on real data. **These four constants ARE still used elsewhere for a genuinely different, narrower purpose** — see the Internal Mechanics note on `_compact_if_node`'s fire/continuation selection below; do not read "superseded for qualification" as "superseded everywhere in this file."

## AC-6 Bug Fixes (two independent structural defects, both closed at `bf6f026b`)

Beyond the discriminator/direction correction above, the AC-6 rebuild fixed two independent traversal bugs in the wave-1 code, found during this cycle's diagnosis:

1. **The early-continue bug (`_find_cascade_roots`) — the largest single contributor to the shipped detector's miss rate.** The wave-1 walk stopped descending into BOTH of a found cascade root's branches entirely once it matched. Real trees chain many independent FR gates as sibling if/elif-via-else ladders; stopping at the first match orphaned every sibling gate further down the chain from ever being scanned. Fixed: the walk now continues into the root's CONTINUATION (else) branch only — never re-scanning the fire (true) branch, whose own nested tiers are resolved separately by `_build_cascade_overlay`'s own tier-walk, avoiding duplicate tier reports.
2. **A second, independent overbought-range filter inside `_build_cascade_overlay`'s `_compact_if_node`.** Beyond the root-qualification filter (fixed above), a SECOND copy of the same range check lived inside the overlay-construction path and was silently excluding a cascade's own genuine threshold from its reported `rsi_thresholds` even after the node correctly qualified as a cascade root. Fixed alongside the qualification-path change — `_compact_if_node` now reports every parseable threshold, root or nested tier alike, with the "is this a good signal" judgment left entirely to AC-4's data-driven classification against real Atlas edge stats, never a structural heuristic inside the detector.

Both fixes were traced and confirmed by fr-engine during GREEN implementation; see `DE-FR-SIGNALS-001` for the commit-cited narrative.

**A third fix was proposed, tried, and FALSIFIED — recorded here so it is never retried blind (fr-test, commit `7ca7c0c6`).** A team-lead-ratified hypothesis proposed extending direction-explicit fire-branch IDENTIFICATION (not just qualification) into `_compact_if_node`'s own fire/continuation split. Applying it introduced 3 NEW test failures (`real_tree_04`, `real_tree_09`, VIX-ticker-presence assertions) by mislabeling a STUBBED continuation branch as "fire" on cascades where the raw if-node's condition-side happened to be the larger, continuation-bound side. Root cause: `_compact_if_node`'s fire/continuation split (`fire_child = cond_child if cond_n <= else_n else else_child`) is INTENTIONALLY size-based, answering a genuinely different question from AC-3's direction-explicit walk — see the Internal Mechanics note below. The fix was reverted; the size-based split remains correct and load-bearing for this specific purpose.

## Named Constants (calibration — current + superseded)

| Name | Value | Status | Basis |
|------|-------|--------|-------|
| `VIX_FAMILY_TICKERS` | `{VIXY, VIXM, UVXY, UVIX, VXX, SVXY, SVIX}` | **Active** — used by both `detect_frontrunner_cascades` and `extract_fr_checks` | Grounding note: "fire baskets always contain >=1 VIX-family instrument but not always VIXY" |
| `_RSI_FN_SUBSTRINGS` | `("relative-strength-index", "rsi")` | **Active** — `_is_rsi_condition`, cascade-rung qualification only | Real trees use `"relative-strength-index"`; substring match tolerates naming drift |
| `_CORE_PLACEHOLDER_PREFIX` | `"CORE_ASSET_"` | **Active** — defensive fixture-corruption check | Fixture-only marker; flags an overlay that swallowed stubbed core content with no VIX ticker of its own |
| `_SIZE_CLIFF_MAX_RATIO` | `0.30` | **Superseded for cascade-root QUALIFICATION** (`_qualifies_as_cascade_rung` no longer references it — kept as the AC-6 regression-test anchor); **still ACTIVE** in `_is_internal_hedge_subgate`'s narrower question (see below) | Wave-1 calibration against the 11 real trees; empirically falsified this cycle for qualification (see "Superseded" above) |
| `_SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES` | `40` | **Superseded for qualification; still ACTIVE** in `_is_internal_hedge_subgate` | Wave-1 calibration; empirically falsified this cycle for qualification |
| `_RSI_OVERBOUGHT_MIN` / `_MAX` | `50.0` / `100.0` | **Superseded** — unused by the qualifying path; kept as the AC-6 regression-test anchor | Wave-1 grounding note; empirically falsified this cycle (rejected real sub-50 thresholds outright) |

`_is_internal_hedge_subgate` (a distinct, still-active helper for compacting non-RSI hedge sub-gates found INSIDE an already-confirmed fire branch, e.g. `cumulative-return(UVXY) lt 5.5` deciding de-escalation) still uses `_SIZE_CLIFF_MAX_RATIO`/`_SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES` for its own, narrower "is this hedge-internal machinery" question — this is a different question from cascade-ROOT qualification and was not in scope for the AC-6 rebuild; it is not itself a claim about which branch fires VIX at the root level. `_compact_if_node`'s OWN fire/continuation split (see Internal Mechanics) ALSO still uses node-count comparison directly (not these two named constants, but the same size-based reasoning) — see the falsified-third-fix note in AC-6 Bug Fixes above.

## Public Types

### `Cascade` (dataclass) — unchanged from wave-1

One detected leading frontrunner cascade (possibly multi-tier). Fields: `overlay_tree` (dict), `rsi_thresholds` (`list[float]`), `vix_tickers` (`set[str]`), `group_name` (`str | None`).

### `DetectionResult` (dataclass) — unchanged from wave-1

Returned by `detect_frontrunner_cascades`. Never `None`. Fields: `cascades` (`list[Cascade]`, may be empty), `skip_reason` (`str | None`, set whenever `cascades` is empty; never both empty AND `None`).

### `FRCheck` (dataclass) — NEW this cycle (AC-3)

Returned by `extract_fr_checks` — one per condition node whose TRUE branch reaches a VIX-family ticker.

| Field | Type | Description |
|-------|------|--------------|
| `fr_key` | `str \| None` | `"{ticker}:{window}:{raw_rhs_val}"` for a genuine fixed-threshold check; `None` for a crossover |
| `ticker` | `str` | The LHS subject ticker |
| `fn` | `str` | The LHS indicator function name (verbatim from the tree, e.g. `"relative-strength-index"`) |
| `window` | `int \| None` | Resolved via `_get_window` — handles both real-tree conventions (`lhs-fn-params.window` int, `lhs-window-days` string fallback) |
| `comparator` | `str` | Verbatim from the tree (e.g. `"gt"`) |
| `threshold` | `float \| None` | The parsed numeric threshold for a genuine fixed check; `None` for a crossover |
| `vix_tickers` | `frozenset[str]` | Every VIX-family ticker found in this condition's TRUE branch |
| `branch_path` | `list[str]` | The actual root-to-node ancestry of `"true"`/`"false"` hops taken to reach this condition — direction is read directly, never inferred |
| `node_id` | `str \| None` | The condition child's own `id` |
| `group_name` | `str \| None` | The enclosing parallel sub-strategy's group name, or `None` at the tree root |
| `rhs_fn` | `str \| None` | **Invariant:** exactly one of `{fr_key, rhs_ticker}` populated, never both, never neither. `rhs_fn` may populate alongside `rhs_ticker` as enrichment (never alongside `fr_key`) |
| `rhs_val` | `str \| None` | No live population path under the corrected discriminator — always `None` in practice (kept for shape stability) |
| `rhs_ticker` | `str \| None` | The RHS ticker for a genuine ticker-vs-ticker crossover; `None` for a genuine fixed-threshold check |

## API Reference

### `detect_frontrunner_cascades(tree: dict) -> DetectionResult`

The cascade-detection public entry point (AC-2/AC-6). Walks the whole tree (`_find_cascade_roots`), builds a compact overlay for each qualifying cascade root (`_build_cascade_overlay`), and returns the aggregate result.

**Returns:** `DetectionResult` — never raises (D-1); a malformed tree degrades to `cascades=[], skip_reason="invalid tree: not a dict"`, or `skip_reason="detector error: <type(exc).__name__>"` on any unexpected internal error.

**Skip reasons:** `"no incumbent frontrunner cascade detected — no if-node with an RSI-gated condition whose smaller branch contains a VIX-family ticker was found anywhere in the tree"` (docstring wording predates the AC-6 rebuild's direction-explicit criterion — the reason string itself was not touched by AC-6 and describes the OLD size-based framing; functionally it still fires correctly under the new rule, just with stale wording — flagged here rather than silently glossed over) · `"candidate cascade roots were found but all failed validation"` — a defensive fallback if every found root's built overlay turned out corrupted · `"detector error: RecursionError"` — historical; closed by the P2-1 iterative-traversal hardening (wave-1), the honest-degradation contract at this boundary is unchanged.

---

### `extract_fr_checks(tree: dict) -> list[FRCheck]`

**AC-3, new this cycle.** Walks the WHOLE tree; every condition node whose TRUE branch reaches a VIX-family ticker yields ONE (or more, for a `binary-compound` broadcast — see below) `FRCheck`. Direction is read directly off each ancestor if-node's `is-else-condition?`, never inferred, never size-based. Self-referential VIX-timing gates (subject ticker itself VIX-family) yield NO `FRCheck` at all — silent omission, not a partial/flagged record.

Fn-agnostic and comparator-agnostic by design — AC-4's `classify_fr_checks` (`advisors/frontrunner_signals.py`) filters at join time, not this walk. Dispatches across all four real condition shapes:

- **Flat** (`lhs-fn` directly on the if-child) — `_resolve_flat_tuple`.
- **Nested `binary`** (single ticker, `condition.lhs.ticker`) — `_resolve_nested_condition` → `_build_nested_tuple`.
- **Nested `binary-compound`** (broadcasts ONE condition over an N-ticker list, `condition.tickers`) — yields N tuples from ONE physical node.
- **Nested `compound`** (an AND/OR container) — recurses into its own `conditions` list.

**Full traversal:** descends into BOTH branches of every if-node unconditionally, so a qualifying check is never a stopping point — arbitrarily many FR-checks chained via sibling if/elif ladders or nested scale-in tiers are all found.

**Returns:** `list[FRCheck]`, possibly empty. Never raises (D-1) — malformed/`None` input degrades to `[]`.

**Example:**
```python
from advisors.frontrunner_detector import extract_fr_checks

checks = extract_fr_checks(symphony_tree)
for c in checks:
    if c.fr_key:
        print("genuine:", c.fr_key, c.branch_path)
    else:
        print("non-joinable:", c.ticker, c.rhs_ticker or c.rhs_fn, c.branch_path)
```

## Internal Mechanics

- **`_get_condition_branch_pair`** — unchanged in signature from wave-1, now the single shared primitive both `_qualifies_as_cascade_rung` and `extract_fr_checks` resolve through.
- **`_get_window`** — resolves a flat condition node's LHS window across BOTH real-tree conventions: `lhs-fn-params.window` (int) and `lhs-window-days` (string fallback). A walk that only reads one convention silently mis-keys or misses checks using the other (fr-test's extraction gotcha, found 2026-07-16).
- **`_is_ticker_comparison`** — the verdict-confirmed discriminator: `rhs-fixed-value?` explicitly `False` (see the discriminator section above for the full falsification story).
- **`_count_nodes`, `_collect_tickers`, `_find_cascade_roots`** — iterative (explicit-stack), mirroring `symphony_schema.py`'s established pattern (P2-1, wave-1 hardening) — a very deep real tree never triggers `RecursionError`. Unchanged by the AC-3/AC-6 rebuild except `_find_cascade_roots`'s continuation-branch behavior (see AC-6 Bug Fixes above).
- **`_build_cascade_overlay`/`_compact_if_node`/`_compact_subtree`** — the overlay-construction recursion (operating only on the already-small compact cascade subtree, not the full symphony) — left recursive, not in scope for the P2-1 or AC-6 hardening passes. **`_compact_if_node`'s fire/continuation branch SELECTION is INTENTIONALLY size-based** (`fire_child = cond_child if cond_n <= else_n else else_child`, line ~625) — this is a DIFFERENT question from AC-3/AC-6's direction-explicit QUALIFICATION rule and was deliberately left untouched by the rebuild. The continuation stub is deliberately padded to `>= fire_node_count + 1` specifically so downstream consumers can keep re-deriving "which branch is fire" from relative size on the returned overlay. A team-lead-ratified hypothesis to make this selection direction-based too was tried and falsified (fr-test, `7ca7c0c6`) — see the AC-6 Bug Fixes section's third entry. Applying the AC-3 model here would be a category error: qualification (AC-6, this section's canonical rule) and fire-branch labeling-for-display (this specific size-based split) are genuinely different questions that happen to usually agree.
- **Known open completeness gap (`_compact_subtree`'s verbatim-copy path, production code, not a doc issue) — flagged here for reader awareness, not yet resolved as of this writing.** fr-test traced (commit `7ca7c0c6`) a real case on `real_tree_04`/`real_tree_06` where a nested if-node that does NOT independently qualify as a cascade rung or an internal hedge sub-gate (e.g. a BND-vs-SH ticker crossover, correctly failing `_qualifies_as_cascade_rung`'s `_parse_rsi_threshold` check) is copied VERBATIM by `_compact_subtree` — and that verbatim-copied region can itself contain BOTH a genuine nested RSI tier (which should have its own large branch stubbed) AND unrelated `CORE_ASSET_` placeholder sibling content, leaking core-strategy structure into the overlay. Left as a correctly-RED test assertion (`test_detected_cascade_fire_branch_never_includes_a_core_asset_placeholder`, 2 params) pending a fr-engine/team-lead verdict — not fixed in this doc pass, not silently loosened as a test premise.

## Testing

- `tests/advisors/test_frontrunner_detector.py` — 10 wave-1 test functions (63 parametrized cases after `7ca7c0c6`, validated against the operator's 11 real captured `/score` trees). **The AC-6 rebuild cycle-caused 3 stale test clusters (12 parametrized failures total, verified by fr-test as genuinely cycle-caused — the fork point itself passed clean).** Resolution (commit `7ca7c0c6`, direct reproduction of all 12 before any change): **Cluster 1 (9 params, FIXED)** — `test_detected_cascade_rsi_thresholds_fall_in_the_grounded_range` hardcoded the pre-rebuild `50 <= threshold <= 100` grounding note; the discriminator work proves genuine thresholds as low as 31.0 are real, so the range assertion was DELETED (not loosened — the premise is dead, not the numeric bounds), the `comparator=='gt'` assertion kept. **Cluster 2 (1 param, FIXED)** — the pre-verdict carve-out excluding `real_tree_08` (Paragons, believed to have zero genuine cascades) from `test_detects_at_least_one_cascade_on_every_real_tree` was removed once the verdict confirmed it genuine (fires UVIX, `rsi_thresholds=[31.0]`, directly reproduced). **Cluster 3 (falsified and reverted)** — see the AC-6 Bug Fixes section's third entry and the Internal Mechanics note on `_compact_if_node` above; this cluster's tests were reverted to their original size-based assertions, which remain correct. **2 genuinely RED failures remain, left AS-IS** (`test_detected_cascade_fire_branch_never_includes_a_core_asset_placeholder` on `real_tree_04`/`real_tree_06`) — NOT a test-staleness issue; a real production completeness gap in `_compact_subtree`'s verbatim-copy path (see Internal Mechanics above), pending a fr-engine/team-lead verdict per "never blind make-it-pass." Scoped verification at `7ca7c0c6`: `pytest -n0 tests/advisors/test_frontrunner_detector.py` → 63 passed, 2 failed, 0 errors.
- `tests/advisors/test_frontrunner_deep_tree_hardening.py` — 5 wave-1 P2-1 tests (depth-hardening + D-1 boundary regression guards), unaffected by the AC-3/AC-6 rebuild.
- `tests/advisors/test_frontrunner_extraction_walk.py` — AC-3 (`extract_fr_checks`), 15 tests: direction-explicit extraction on real-tree fixtures, the Paragons ELSE-branch negative control, self-referential exclusion, the fixed-threshold/crossover discriminator (including the two-stage falsification's resolved population-wide invariant sweep), condition-shape dispatch (flat/binary/binary-compound/compound).
- `tests/advisors/test_frontrunner_detector_ac3_rebuild.py` — AC-6 (`detect_frontrunner_cascades` rebuild), 12 tests: the corrected 8-symphony `SPY:10:31` expected-set, the `5Xjz`/BTAL-only negative control, the old-signature 0-match regression guard.

## Internal Dependencies

- `copy`, `dataclasses`, `logging` — stdlib only. No imports from `database`, `symphony_schema`, `alpha_bot_execution`, or any network/execution module — this module is a pure, side-effect-free tree walk.

## Consumers

- `advisors/frontrunner_builder.py` — `_run_build_for_symphony` calls `detect_frontrunner_cascades` on each live symphony's `/score` tree (unchanged call site from wave-1); the AC-5 builder-gating background compute path calls `extract_fr_checks` (new this cycle) to feed `advisors/frontrunner_signals.classify_fr_checks`.
- `advisors/frontrunner_signals.py::classify_fr_checks` — the sole consumer of `extract_fr_checks`'s output shape (AC-4 join).
