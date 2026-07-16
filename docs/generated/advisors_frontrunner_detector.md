# advisors/frontrunner_detector

> Locates a symphony's incumbent frontrunner overlay(s) — leading RSI-overbought → VIX/hedge cascades — in a live Composer `/score` tree, and extracts every per-condition FR-check for signal classification. Cascade QUALIFICATION and FR-check extraction are direction-explicit (TRUE branch reaches a VIX-family ticker); the overlay-construction fire/continuation SELECTION step remains intentionally size-based — two deliberately different models for two deliberately different questions, confirmed by two independent, later-corrected attempts to unify them (see "AC-6 Bug Fixes" below).

**Source:** `advisors/frontrunner_detector.py`
**Last updated:** 2026-07-16 (AC-3/AC-6 rebuild GREEN at `bf6f026b`; a size→direction migration of the overlay-construction compaction logic was independently derived, applied, and reverted TWICE — once by fr-test as a falsified hypothesis (`7ca7c0c6`), once by fr-engine as an independent re-derivation of the same falsified hypothesis (`101ad377`, reverted at `42ffe560`) — before the real leak was fixed by a narrower, fixture-domain-only per-child purification plus a genuine production fix to a false-positive filter (`42ffe560`); a fresh-tree cross-check then confirmed the underlying leak is REAL in production, not a fixture artifact, and remains an open, low-severity, scoped limitation (`0ab3ae78`). See `DE-FR-SIGNALS-001` in `DECISIONS.md` for the full narrative with commit citations. Supersedes the 2026-07-11 wave-1 size-cliff signature described below, preserved as a dated historical section, not deleted.)

## Overview

`frontrunner_detector.py` has two public entry points doing related but distinct jobs:

- **`detect_frontrunner_cascades(tree)`** (feature-plans/frontrunner-builder.md AC-2, rebuilt this cycle per feature-plans/frontrunner-signals.md AC-6) — finds the leading cascade(s) a symphony already has, for the Frontrunner Builder's detect→generate→splice→gate→accept pipeline (`docs/generated/advisors_frontrunner_builder.md`).
- **`extract_fr_checks(tree)`** (feature-plans/frontrunner-signals.md AC-3, new this cycle) — walks the WHOLE tree and returns every individual FR-check condition, for joining against live Atlas signal data (`docs/generated/advisors_frontrunner_signals.md`).

**Cascade-root QUALIFICATION (`_qualifies_as_cascade_rung`) and FR-check extraction (`extract_fr_checks`) are unified on the identical direction-explicit rule: a condition node's TRUE branch reaching a VIX-family ticker.** This part of the AC-6 rebuild is settled and unchanged since `bf6f026b`.

**The overlay-construction fire/continuation SELECTION step (which of a qualifying node's two branches becomes the reported `Cascade.overlay_tree`'s fire content) is a DIFFERENT question, and remains intentionally size-based** (`_is_internal_hedge_subgate`, `_compact_if_node`) — this was tried as a migration to direction-explicit TWICE this cycle, independently, and both attempts were reverted after real evidence showed size is the more reliable signal for this specific job: a root can legitimately qualify via "TRUE reaches VIX somewhere in its subtree" while still being a large, mostly-core branch with one deeply buried VIX reference — direction alone cannot tell you which side is the compact hedge basket in that case, but size (still) can. See "AC-6 Bug Fixes" below for the full two-attempt story, including the real, still-open production limitation that emerged from the second attempt's investigation.

Off-execution-path. Advisory-only (read-only tree walk; no writes, no network). Never raises (D-1) on either public entry point — a malformed tree degrades to an empty result with a reason (`detect_frontrunner_cascades`) or an empty list (`extract_fr_checks`).

## Detection Signature (canonical, AC-3/AC-6, `bf6f026b` — unaffected by the fire/continuation saga below)

A condition node qualifies — for cascade-rung purposes (`_qualifies_as_cascade_rung`) AND for FR-check extraction (`extract_fr_checks`) alike — when:

1. It has exactly two `if-child` entries, identified primarily via `is-else-condition?: False`/`True`, with a fallback (`_get_condition_branch_pair`) to "whichever child actually carries a condition" when that marker is absent.
2. **The condition's TRUE branch (the `is-else-condition?: False` child) reaches at least one VIX-family ticker anywhere in its subtree** (`_collect_tickers(cond_child) & VIX_FAMILY_TICKERS`) — this is the entire qualifying signal. Branch SIZE is never consulted for this question, in either direction.
3. (Cascade-rung qualification only, `_qualifies_as_cascade_rung`) the condition is additionally a flat RSI-family condition (`lhs-fn` matches `"relative-strength-index"`/`"rsi"`), `comparator == "gt"`, and has a parseable fixed numeric threshold (`_parse_rsi_threshold`, see the discriminator below). `extract_fr_checks` is deliberately fn-agnostic and comparator-agnostic at the walk level — filtering by fn/comparator is AC-4's join-time job (`classify_fr_checks` in `advisors/frontrunner_signals.py`), not this module's.
4. (Root-scan cascade-rung qualification only) the condition is not self-referential — it does not watch a VIX-family instrument's own indicator (`_is_self_referential_timing_gate`).

**Full traversal, not stop-on-match.** Both `_find_cascade_roots` and `extract_fr_checks` descend into BOTH branches of every `if`-node unconditionally — a qualifying node is never a stopping point. Real trees chain many independent FR gates as sibling if/elif-via-else ladders; a walk that stops at the first match misses everything downstream (the single largest defect in the wave-1 detector — see AC-6 Bug Fixes).

A cascade may still recurse into its own fire branch to resolve **scale-in tiers** — the whole tiered chain is reported as ONE `Cascade`, its `overlay_tree` spanning every tier.

## The fixed-threshold vs. crossover discriminator (two-stage falsification — read this before trusting any RHS-shape claim elsewhere in the docs)

A flat condition's RHS can encode either a genuine fixed numeric threshold (`RSI(SPY,10) gt 31`) or a ticker-vs-ticker relative-strength comparison (`RSI(LQD,50) gt RSI(XLV,50)`). Distinguishing the two correctly matters for both AC-3 (which of `FRCheck.fr_key` or `FRCheck.rhs_ticker` gets populated) and AC-6 (whether `_parse_rsi_threshold` returns a real threshold or `None`).

1. **Stage 1 (falsified).** An initial analysis-layer hypothesis proposed: "a condition whose RHS carries an `rhs-fn` key is a crossover, never a fixed threshold." Produced an incorrect 8→5 correction to the operator's original genuine-`SPY:10:31` symphony count and a table of "21 contaminated fr_keys."
2. **Stage 2 (verdict, `.claude/fr-signals-inputs/mirror-pattern-verdict.md`, commit `93e27efb`).** A second, independent falsification pass (fr-falsifier2) traced the disputed nodes directly and found the Stage-1 rule itself falsified: `rhs-fn`/`rhs-window-days` are vestigial echoes from a non-canonical Composer export pathway, present on both genuine fixed-threshold and genuine crossover nodes alike, zero correlation to which one a node actually is.
3. **The corrected, exceptionless discriminator (`_parse_rsi_threshold`/`_is_ticker_comparison`, `bf6f026b`):** a node is a fixed-threshold check IFF `rhs-val` parses as a number. A ticker `rhs-val` is a genuine crossover. `rhs-fn` is NEVER consulted for this question.
4. **Stage 3 — discriminator COMPLETENESS fix (`101ad377`, kept unchanged by the later revert at `42ffe560` — distinct code path from the fire/continuation saga).** `_is_ticker_comparison`'s original `bf6f026b` implementation checked `rhs-fixed-value? is False`, but real trees routinely OMIT that key entirely for a genuine ticker comparison rather than setting it explicitly `False` (confirmed on a real node in `real_tree_04`, id `74083377-ec89-4844-8ec1-d80bc2aae07c`, `rhs-val="SH"`, no `rhs-fixed-value?` key at all). Per the verdict's own population sweep: `False` (385 nodes) and absent/`None` (510 nodes) both pair exceptionlessly with a ticker `rhs-val` (895/895 combined); only `True` (166/166) pairs with a numeric `rhs-val`. The `is False`-only check silently dropped the 510-node absent-key population entirely — `extract_fr_checks` returned no tuple at all for those (neither `fr_key` nor `rhs_ticker`, a fully vanished FRCheck). Fixed to `is not True`. Does not affect the recovery baseline (ticker comparisons never populate `fr_key`) but improves completeness of genuine crossover/`vs()`-form capture.
5. **Final AC-6 expected-set (operator vindicated):** genuine VIX-firing `SPY:10:31` = 8 symphonies (qF5Z/hvPi/INfC/Gpaw/MoAk/iaSO/lW4Z-Paragons/n2oo). `5Xjz` carries the genuine gate too but its TRUE branch routes to BTAL only — a genuine-but-not-FR-check negative control. The "21 contaminated fr_keys" table dissolves entirely.
6. **What genuinely IS real about `rhs-fn`:** for a genuine ticker-vs-ticker crossover, `rhs-fn`/`rhs-fn-params.window` DOES carry real, meaningful RHS-indicator data (verified against `real_tree_06` node `0d98c2bb`: `RSI(LQD,50) gt RSI(XLV,50)`, `rhs-fn-params.window=50` matching the LHS window exactly). `FRCheck`'s invariant: exactly one of `{fr_key, rhs_ticker}` populated; `rhs_fn` MAY populate alongside `rhs_ticker` as enrichment, never alongside `fr_key`. **Known display-fidelity limitation (deferred, not a bug):** `FRCheck` captures `rhs_fn` but NOT `rhs_window` — fr-engine deliberately declined to add the field since no RED test drives it (TDD minimalism). The `vs()` display form currently renders `LQD:50:vs(XLV)`, not the fully-faithful `vs(rsi(XLV,50))`. Tracked as a follow-up needing a new RED test — see `DE-FR-SIGNALS-001` residuals.

## Superseded: the wave-1 size-cliff signature for QUALIFICATION (2026-07-11, PR #96 — historical, not in use for qualification)

The originally-shipped detector used a size-based signature for cascade-root QUALIFICATION: a candidate `if` node qualified when the SMALLER of its two branches (by node count) contained a VIX-family ticker, subject to a ratio check (`small_n / large_n <= _SIZE_CLIFF_MAX_RATIO=0.30`) or an absolute check (`small_n <= _SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES=40`), plus a plausible-RSI-overbought-range floor (`_RSI_OVERBOUGHT_MIN=50.0`/`_RSI_OVERBOUGHT_MAX=100.0`). **This is empirically falsified for QUALIFICATION and preserved in the source ONLY as a named regression-test anchor** — `_qualifies_as_cascade_rung` no longer references any of the four constants. Two measured defects: the genuine `SPY:70:62` node in `real_tree_04` has its fire branch on the LARGER side by node count (51 vs. 43), and `SPY:10:31`/`SPY:21:30`'s real thresholds (31.0/30.0) both fail the `50.0` overbought floor regardless of direction.

**Important — do not conflate this with the overlay-construction fire/continuation SELECTION question.** `_is_internal_hedge_subgate` and `_compact_if_node`'s own branch-selection logic use the SAME two size-cliff constants, but for a DIFFERENT purpose (deciding which qualifying node's branch is fire content for the returned overlay) — and that use is **still active and correct**, not superseded. See Named Constants and AC-6 Bug Fixes below.

Combined, the QUALIFICATION defects account for the shipped detector recovering **0 of 11 real trees' cascades** at the AC-2 wave-1 milestone and the low **44 of 550 fr_key MEMBERSHIPS (8.0%)** recovery rate measured against the corrected AC-3 expected-set this cycle (fr-engine-derived, fr-doc-spot-verified on two trees — see `DE-FR-SIGNALS-001` for the full number, including the unit clarification: 550 is fr_key memberships summed across all 11 trees — symphony×fr_key pairs, double-counting a key present in multiple trees — NOT the 165 distinct fr_keys that actually exist, and NOT the 1,704 individual FRCheck rows `extract_fr_checks` returns across all condition shapes including non-joinable crossovers).

## AC-6 Bug Fixes (settled QUALIFICATION fixes, plus the two-attempt overlay-construction saga, plus the genuine remaining production limitation)

**Settled, unaffected by anything below:**

1. **Defect #1 — the early-continue bug (`_find_cascade_roots`), `bf6f026b`.** The wave-1 walk stopped descending into BOTH of a found cascade root's branches once it matched, orphaning every sibling gate further down an if/elif-via-else chain — the largest single contributor to the miss rate. Fixed: the walk continues into the root's CONTINUATION (else) branch only.
2. **Defect #2 — a second overbought-range filter inside `_compact_if_node`, `bf6f026b`.** A duplicate of the QUALIFICATION range check lived inside the overlay-construction path, silently excluding a cascade's own genuine threshold from `rsi_thresholds` even after correct qualification. Fixed alongside the qualification-path change.

**The two-attempt overlay-construction saga (fire/continuation SELECTION — a genuinely different question from qualification):**

3. **Attempt 1 — proposed, tried, and FALSIFIED (fr-test, `7ca7c0c6`).** A team-lead-ratified hypothesis proposed migrating `_compact_if_node`'s fire/continuation SELECTION (not just qualification) to direction-explicit. Applying it introduced 3 new test failures by mislabeling a stubbed continuation branch as "fire" whenever the condition-side happened to be the larger, continuation-bound side. Reverted.
4. **Attempt 2 — INDEPENDENTLY re-derived and re-applied, then re-falsified (fr-engine, `101ad377`, reverted at `42ffe560`).** fr-engine committed the SAME migration independently, before reading team-lead's queued messages and fr-test's `7ca7c0c6` finding — a second, unrelated derivation of the identical wrong hypothesis. fr-engine's own root-cause explanation for why size, not direction, is the correct signal here: a root can legitimately qualify via `_qualifies_as_cascade_rung`'s "TRUE reaches VIX somewhere in its subtree" while still being a large, mostly-core branch with one deeply buried VIX reference — size, not direction, is the more reliable signal for "this side IS the compact hedge basket" on the operator's real trees. Reverted, byte-identical to the pre-`101ad377` original (fr-review-confirmed). **Two independent people deriving and then correctly retracting the identical wrong migration is read as evidence FOR the "these are genuinely two different models by design" framing, not an embarrassment** — it demonstrates the failure mode is a natural, recurring temptation (unify everything on the newer, shinier rule) that real evidence twice pushed back on.
5. **The actual leak fix — redesigned as a per-child, symmetric purification inside `_compact_subtree`'s generic recursion (`42ffe560`). FIXTURE-DOMAIN ONLY — do not cite as production protection.** After each nested child is recursively compacted, if the result still carries a `_CORE_PLACEHOLDER_PREFIX` (`"CORE_ASSET_"`) placeholder anywhere AND has zero VIX-family ticker anywhere within it, that one child is replaced with a stub. This never discards a child with any real VIX content, so it cannot reproduce attempts 1/2's failure mode, and does not touch the ratified size-based selection model at all — it resolved the original `real_tree_04` BND-vs-SH leak, the `INfCn`/`hvPi` sibling core-vs-core allocation leak, and the `real_tree_09`/n2oo inverted-polarity leak, all without content loss, when tested against the FIXTURE trees. **fr-review falsified the claim that this same fix protects production consumers** (specifically `advisors/frontrunner_builder.py::_collect_step_keyed_signal_tickers`, which reads the same compacted `overlay_tree` to build the Fable generation prompt's `watched_tickers`): `_CORE_PLACEHOLDER_PREFIX` is a fixture-only synthetic anonymization marker — confirmed via direct source read, no real Composer ticker ever carries it — so the purification check structurally CANNOT fire against production data. Valuable as a fixture-domain regression guard (`tests/advisors/test_frontrunner_detector.py::test_watched_tickers_derivation_never_leaks_a_core_asset_placeholder`, line 671), never citable as evidence the production consumer is protected.
6. **Defect #6 — a genuine, non-fixture-scoped production fix: the false-positive cascade filter in `detect_frontrunner_cascades` (`42ffe560`).** Once defect #5's purification started cleaning fixture-domain leaks up BEFORE the top-level filter ran, a pre-existing defensive check (`_has_core_placeholder(...) AND zero VIX anywhere = corrupted, skip`) stopped firing on 3 confirmed non-hedge overlays (real_tree_09-adjacent leveraged-ETF baskets with genuinely zero VIX content), letting them slip through as false-positive cascades. Fixed by tightening the filter to the real invariant directly — **zero VIX-family ticker anywhere in the overlay = not a cascade, full stop** — independent of the now-partially-unreliable core-placeholder proxy. This check keys on `VIX_FAMILY_TICKERS`, not `_CORE_PLACEHOLDER_PREFIX`, so unlike defect #5 it IS a genuine production-reachable fix.

**The confirmed-REAL, currently OPEN production limitation (fresh-tree cross-checked, `0ab3ae78`):**

The `real_tree_04` BND-vs-SH leak this entire saga started from was independently cross-checked against `.claude/fr-signals-inputs/fresh-trees-0716/INfCn3eKsu6i4oTTqdUp.json` (a genuinely fresh, non-fixture-trimmed pull) at the exact same node id. **CONFIRMED REAL, not a trimming artifact:** the fresh tree's genuine (unanonymized) tickers at that node are `EDV`/`KMLM`/`TQQQ`/`UPRO`/`VT` — real core-strategy holdings, sitting in the exact branch structure the trimmed fixture had correctly (not erroneously) anonymized to `CORE_ASSET_` placeholders. **The fixture was faithful; it was never the source of this issue.** (A SEPARATE, genuinely real trimming defect — hedge-ticker over-scrubbing — was found independently by fr-falsifier3 and fixed for 7 of 11 fixtures at `aad7c49b`; that is a different, already-resolved issue from this one.)

Because the production-firing per-child purification (defect #5) is fixture-domain-only, **real core-strategy tickers CAN reach the reported fire-basket / `watched_tickers` content on multi-tier cascades with nested crossovers, in production, today.** Severity assessment: **LOW.** `_collect_step_keyed_signal_tickers`'s output feeds ONLY the Fable candidate-generation prompt as a hint of which tickers to watch — it never reaches a trade decision, the cull/classification pipeline, or AC-3/AC-4's correctness. The cull/classification pipeline (`extract_fr_checks`, `classify_fr_checks`) walks the ORIGINAL, untrimmed tree directly — never the compacted `Cascade.overlay_tree` — so this limitation cannot affect which fr_keys get classified or which candidates get gated.

**Residual, explicitly next-cycle scope, needs its own A/C:** the real fix requires marker-free core-content identification — production trees carry no `CORE_ASSET_`-style tag to key off, so distinguishing genuine hedge content from genuine core-strategy content structurally, without a synthetic marker, is a real, unsolved design question. fr-test is adding an `xfail(strict=False)` tripwire test so this limitation stays executable and visible (will flip to an unexpected-pass and demand attention if the underlying gap is ever closed) — not yet landed as of this writing; cite the specific test once it lands rather than the generic description here.

## Named Constants (calibration — current status by USE SITE, not blanket "superseded")

| Name | Value | Status | Basis |
|------|-------|--------|-------|
| `VIX_FAMILY_TICKERS` | `{VIXY, VIXM, UVXY, UVIX, VXX, SVXY, SVIX}` | **Active** — used throughout: qualification, extraction, hedge-subgate recognition, AND (as of `42ffe560`) `detect_frontrunner_cascades`'s false-positive filter | Grounding note: "fire baskets always contain >=1 VIX-family instrument but not always VIXY" |
| `_RSI_FN_SUBSTRINGS` | `("relative-strength-index", "rsi")` | **Active** — `_is_rsi_condition`, cascade-rung qualification only | Real trees use `"relative-strength-index"`; substring match tolerates naming drift |
| `_CORE_PLACEHOLDER_PREFIX` | `"CORE_ASSET_"` | **Active, but CONFIRMED FIXTURE-ONLY** — used by `_has_core_placeholder`, `detect_frontrunner_cascades`'s defensive check, and `_compact_subtree`'s per-child purification (defect #5). Directly confirmed (not assumed) via this cycle's fresh-tree cross-check that no real Composer ticker ever carries this prefix — any use of this constant is inherently fixture-domain-only, never production-reachable | Fixture-only synthetic anonymization marker |
| `_SIZE_CLIFF_MAX_RATIO` | `0.30` | **Superseded for cascade-root QUALIFICATION only** (`_qualifies_as_cascade_rung` doesn't reference it); **STILL ACTIVE and correct** in `_is_internal_hedge_subgate`'s and `_compact_if_node`'s fire/continuation SELECTION — confirmed by two independent, later-reverted attempts to remove this use, both of which introduced real regressions | Wave-1 calibration against the 11 real trees; falsified for qualification, validated (twice, by elimination) for branch-selection |
| `_SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES` | `40` | Same status as above | Same basis as above |
| `_RSI_OVERBOUGHT_MIN` / `_MAX` | `50.0` / `100.0` | **Fully superseded** — unused by the qualifying path; kept as the AC-6 regression-test anchor | Wave-1 grounding note; empirically falsified this cycle |

**Correction to two earlier revisions of this doc:** a version matching `bf6f026b`/`7ca7c0c6` correctly said the size-cliff constants were still active in branch selection. A LATER version (matching `101ad377`, briefly committed) incorrectly said they were fully superseded — that was accurate only for the brief window `101ad377` was live, and was itself reverted at `42ffe560`. This is the third and (as of this writing) final revision of this table; it matches the code at `42ffe560`/`0ab3ae78`, directly source-verified.

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
| `fn` | `str` | The LHS indicator function name |
| `window` | `int \| None` | Resolved via `_get_window` — handles both real-tree conventions |
| `comparator` | `str` | Verbatim from the tree (e.g. `"gt"`) |
| `threshold` | `float \| None` | The parsed numeric threshold; `None` for a crossover |
| `vix_tickers` | `frozenset[str]` | Every VIX-family ticker found in this condition's TRUE branch |
| `branch_path` | `list[str]` | Root-to-node ancestry of `"true"`/`"false"` hops — direction read directly, never inferred |
| `node_id` | `str \| None` | The condition child's own `id` |
| `group_name` | `str \| None` | The enclosing parallel sub-strategy's group name, or `None` at the tree root |
| `rhs_fn` | `str \| None` | Invariant: exactly one of `{fr_key, rhs_ticker}` populated. `rhs_fn` may populate alongside `rhs_ticker` as enrichment (never alongside `fr_key`) |
| `rhs_val` | `str \| None` | No live population path — always `None` in practice |
| `rhs_ticker` | `str \| None` | The RHS ticker for a genuine crossover; `None` for a genuine fixed-threshold check |

## API Reference

### `detect_frontrunner_cascades(tree: dict) -> DetectionResult`

The cascade-detection public entry point (AC-2/AC-6). Walks the whole tree (`_find_cascade_roots`), builds a compact overlay for each qualifying cascade root (`_build_cascade_overlay`), and returns the aggregate result.

**Returns:** `DetectionResult` — never raises (D-1); a malformed tree degrades to `cascades=[], skip_reason="invalid tree: not a dict"`, or `skip_reason="detector error: <type(exc).__name__>"` on any unexpected internal error.

**Skip reasons:** `"no incumbent frontrunner cascade detected..."` (docstring wording predates the AC-6 rebuild, describes the old size-based framing — functionally still fires correctly, just stale wording) · `"candidate cascade roots were found but all failed validation"` · `"detector error: RecursionError"` — historical.

---

### `extract_fr_checks(tree: dict) -> list[FRCheck]`

**AC-3, new this cycle.** Walks the WHOLE tree; every condition node whose TRUE branch reaches a VIX-family ticker yields ONE (or more, for a `binary-compound` broadcast) `FRCheck`. Direction read directly off `is-else-condition?`, never inferred, never size-based. Self-referential VIX-timing gates yield NO `FRCheck` at all.

Fn-agnostic and comparator-agnostic by design. Dispatches across flat, binary, binary-compound, and compound condition shapes. Full traversal: descends into BOTH branches of every if-node unconditionally.

**Returns:** `list[FRCheck]`, possibly empty. Never raises (D-1).

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

- **`_get_condition_branch_pair`** — the single shared primitive `_qualifies_as_cascade_rung`, `extract_fr_checks`, `_is_internal_hedge_subgate`, and `_compact_if_node` all resolve through.
- **`_get_window`** — resolves a flat condition node's LHS window across both real-tree conventions (`lhs-fn-params.window` int, `lhs-window-days` string fallback).
- **`_is_ticker_comparison`** — the verdict-confirmed discriminator, `rhs-fixed-value? is not True` as of `101ad377` (kept unchanged by the later `42ffe560` revert — a separate code path from the fire/continuation saga).
- **`_count_nodes`, `_collect_tickers`, `_find_cascade_roots`** — iterative (explicit-stack), P2-1 hardening — a very deep real tree never triggers `RecursionError`.
- **`_is_internal_hedge_subgate`** — **size-based, as originally shipped, confirmed byte-identical to the pre-`101ad377` original as of `42ffe560`** (fr-review-verified). Compacting non-RSI hedge sub-gates (e.g. `cumulative-return(UVXY) lt 5.5` deciding de-escalation) found INSIDE an already-confirmed fire branch: the smaller of the two branches is the fire side, subject to the `_SIZE_CLIFF_MAX_RATIO`/`_SIZE_CLIFF_MAX_ABSOLUTE_FIRE_NODES` checks. Two independent attempts to migrate this to direction-explicit (`7ca7c0c6`, `101ad377`) were both tried and reverted — see AC-6 Bug Fixes.
- **`_build_cascade_overlay`/`_compact_if_node`** — **fire/continuation branch SELECTION is size-based, as originally shipped** (`fire_child = cond_child if cond_n <= else_n else else_child`). The continuation stub is padded to `>=` the fire branch's own node count specifically so downstream consumers can keep re-deriving "which branch is fire" from relative size on the returned overlay — this padding invariant is load-bearing precisely because size, not direction, identifies fire here.
- **`_compact_subtree`** — the fire-branch-internal recursion. As of `42ffe560`: after each nested child is recursively compacted, a PER-CHILD, SYMMETRIC purity check runs — if the compacted result still carries a `_CORE_PLACEHOLDER_PREFIX` placeholder anywhere AND has zero VIX-family ticker anywhere within it, that specific child is replaced with a stub leaf. **Confirmed fixture-domain-only** (see Named Constants) — never discards a child with any real VIX content, so cannot reproduce the falsified attempts' failure mode, but also cannot protect a real production tree the way it protects the test fixtures, since real trees never carry the `CORE_ASSET_` marker this check keys on.

## Testing

Test counts below are fr-doc's own direct runs at HEAD `0ab3ae78` (`python -m pytest -n0 <file>`), not relayed from any commit message, given how many commits in this section's history have reported differing counts.

- `tests/advisors/test_frontrunner_detector.py` — **66 passed, 0 failed** (fr-doc-verified, `0ab3ae78`). History: 10 wave-1 test functions/63 parametrized cases; `7ca7c0c6` fixed 10 of 12 cycle-caused stale-cluster failures (9-param stale-range-assertion deletion, 1-param Paragons carve-out removal), leaving 2 correctly RED (`real_tree_04`/`real_tree_06`, the original leak); `42ffe560`'s redesigned per-child purification closed those 2; `ae097ef6` added a NEW test (`test_watched_tickers_derivation_never_leaks_a_core_asset_placeholder`, line 671) asserting the fixture-domain guard, initially with an overclaiming comment about production protection; `0ab3ae78` corrected that comment (test logic unchanged) to state plainly that the guard is fixture-domain-only and the production limitation is real, open, and currently untested. Net: 0 failures, but this reflects a fixture-domain-clean state, not a production-protected one — see AC-6 Bug Fixes above.
- `tests/advisors/test_frontrunner_deep_tree_hardening.py` — 5 wave-1 P2-1 tests, unaffected by the AC-3/AC-6 rebuild or the fire/continuation saga.
- `tests/advisors/test_frontrunner_extraction_walk.py` + `tests/advisors/test_frontrunner_detector_ac3_rebuild.py` — **27 passed, 0 failed combined** (fr-doc-verified, `0ab3ae78`; 15 + 12 by file, matching the original AC-3/AC-6 counts exactly — unaffected by the fire/continuation saga, which is scoped to `_build_cascade_overlay`'s internals, not extraction or qualification).
- **Broader frontrunner regression battery:** fr-engine reports 193 tests green post-`95dac72c` (Cluster D wiring) across builder/gate-wiring/generation-quality/real-boundary-contract/atlas-patterns/no-live-api/classification/extraction-walk/detector/tab-render. Not independently re-run in full by fr-doc as part of this doc pass — cite fr-review's/fr-test's own final counts in `DE-FR-SIGNALS-001`'s Verification section for the authoritative cycle-close number, not this doc.

## Internal Dependencies

- `copy`, `dataclasses`, `logging` — stdlib only. No imports from `database`, `symphony_schema`, `alpha_bot_execution`, or any network/execution module — this module is a pure, side-effect-free tree walk.

## Consumers

- `advisors/frontrunner_builder.py::_run_build_for_symphony` — calls `detect_frontrunner_cascades` on each live symphony's `/score` tree (unchanged call site from wave-1); as of Cluster D wiring (`95dac72c`) also calls `extract_fr_checks` once per symphony run to feed classification+persistence — see `docs/generated/advisors_frontrunner_builder.md` for the current wiring status.
- `advisors/frontrunner_builder.py::_collect_step_keyed_signal_tickers` — reads a detected `Cascade.overlay_tree` (NOT the original tree) to derive the `watched_tickers` hint fed into the Fable generation prompt. **This is the consumer with the confirmed-real, open, low-severity `CORE_ASSET_`/core-ticker-leak limitation** documented in AC-6 Bug Fixes above — a generation-prompt-hint-only concern, never a correctness issue for cull/classification.
- `advisors/frontrunner_signals.py::classify_fr_checks` — the sole consumer of `extract_fr_checks`'s output shape (AC-4 join) — this pipeline reads the ORIGINAL tree via `extract_fr_checks`, never the compacted overlay, so it is structurally unaffected by anything in the fire/continuation saga above.
