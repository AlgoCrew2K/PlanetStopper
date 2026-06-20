# advisors/build_plan_generator

> Opus Build-Plan Generator for the real Strategy Builder (Component 2 + 2b): uses the Anthropic SDK in structured tool-use mode to emit diverse objective-shaped build-plans expressed in a constrained strategy DSL, validates every proposed ticker against the tradeable membership set, and admits objective-matched Atlas community strategies alongside the generated plans.

**Source:** `advisors/build_plan_generator.py`
**Last updated:** 2026-06-20 (DE-SB-GEN-TRUNCATION: MAX_OUTPUT_TOKENS=16384 + MAX_GENERATION_ATTEMPTS=3 truncation-retry)

## Overview

`advisors/build_plan_generator.py` is the Opus-backed brain of the real Strategy Builder. It produces the plans that the Component 3 compiler translates into Composer trees, replacing the 7-template stamper in `_generate_candidate_trees` (the engine rewire happens in Component 3).

The module has two responsibilities:

1. **Build-plan generation (AC-7..AC-11).** It calls the Anthropic SDK (`ANTHROPIC_API_KEY`) in structured tool-use mode and receives up to `N_PLANS_PER_OBJECTIVE` diverse build-plans expressed in the build-plan DSL — a JSON-serializable, constraint-typed intermediate representation. It is NOT raw Composer JSON; it is a 1:1 pre-image of the `symphony_schema` constructor API so the compiler in Component 3 is a pure dispatch table with no interpretation. Opus proposes tickers from its own market knowledge; every referenced ticker is then validated against the caller-supplied `membership_set` (a `frozenset` from `universe_provider.get_tradeable_set()`) before a plan is admitted.

2. **Objective-matched Atlas admission (AC-12..AC-13).** It ranks and admits existing Atlas community strategies by objective-relevance (not as an unfiltered top-20), tags them with explicit provenance in `.params["provenance"]`, and pools them with the generated plans for the single downstream FDR gate.

Off-execution-path. Advisory-only. Never raises — every function degrades honestly on failure.

**Scope note:** `advisors/strategy_builder_engine.py` and `advisors/community_strats.py` are NOT modified in this phase. The engine's existing 3-value `Objective` enum stays in place; this module defines its own independent 4-value `Objective`. The engine rewire (_generate_candidate_trees replacement) and community_strats changes land in Component 3.

## The Build-Plan DSL (canonical generator↔compiler contract)

A build-plan is a JSON-serializable `dict`. The generator produces these; the compiler (Component 3, `advisors/plan_tree_compiler.py`) translates each into a valid Composer `raw_value` tree via `symphony_schema` constructors. The DSL is a thin 1:1 pre-image of the constructor API — each `kind`/`scheme` maps to exactly one constructor.

### Top-level build-plan

```python
{
    "plan_id":    str,            # generator-assigned, unique within the run
    "objective":  str,            # one of the 4 Objective values (echoed for provenance)
    "name":       str,            # human label -> make_root name
    "rebalance":  str,            # one of KNOWN_REBALANCE (daily/weekly/monthly/...)
    "provenance": "built-new",    # AC-13: every generator plan tagged built-new
    "root":       NODE            # the single root allocation NODE
}
```

### NODE — tagged union on `kind`

Each `kind`/`scheme` value maps 1:1 to one `symphony_schema` constructor:

| DSL node shape | `symphony_schema` constructor | Notes |
|---|---|---|
| `{kind:"asset", ticker:str}` | `make_asset` | |
| `{kind:"weight", scheme:"equal", children:[NODE...]}` | `make_weight_equal` | |
| `{kind:"weight", scheme:"specified", children:[{node:NODE, pct:number}...]}` | `make_weight_specified` | Children are `{node, pct}` pairs |
| `{kind:"weight", scheme:"inverse_vol", children:[NODE...], window_days:int?}` | `make_inverse_vol` | `window_days` defaults to 30 |
| `{kind:"weight", scheme:"market_cap", children:[NODE...]}` | (producer-deprecated — no constructor added) | Composer retired market-cap weighting (HTTP 422 `node-type-not-supported`; 2026-06-20). The compiler drops any plan with this scheme via `_has_market_cap` before compilation (`reason="market_cap_scheme_deprecated"`). DSL carries the scheme as a forward-compat token; `symphony_schema.KNOWN_STEPS` stays at 16 entries. See `DE-SB-MARKETCAP-DEPRECATED`. |
| `{kind:"group", name:str, children:[NODE...]}` | `make_group` | |
| `{kind:"filter", select_fn:"top"\|"bottom", select_n:int, sort_by_fn:str, window:int, children:[NODE...]}` | `make_filter` | |
| `{kind:"if", condition:{lhs_fn,lhs_ticker,window,comparator,rhs:{fixed:num}\|{ticker,fn,window}}, then:[NODE...], else:[NODE...]}` | `make_if` | Flat condition |
| `{kind:"if_compound", condition:CONDITION, then:[NODE...], else:[NODE...]}` | `make_if_compound` | Recursive CONDITION |

### CONDITION — recursive union on `type`

Used in `if_compound` nodes:

| CONDITION shape | `symphony_schema` constructor |
|---|---|
| `{type:"binary", lhs:{fn,ticker,window}, comparator:str, rhs:{const:num}\|{fn,ticker,window}}` | `make_binary_condition` |
| `{type:"binary_compound", fn:str, tickers:[str...], comparator:str, rhs:{const:num}, window:int, operator:"any"\|"all"}` | `make_binary_compound_condition` |
| `{type:"compound", operator:"any"\|"all", conditions:[CONDITION...]}` | `make_compound_condition` |

### DSL invariants

- Every ticker referenced anywhere in a plan — `asset.ticker`, filter children, `if` condition lhs/rhs tickers, `binary_compound.tickers[]` — is enumerable by the deterministic `plan_tickers()` walk. That walk is BOTH the AC-9 membership-validation surface AND the Component 3 compile target.
- `scheme` ∈ `{"equal", "specified", "inverse_vol", "market_cap"}`
- `comparator` ∈ `symphony_schema.KNOWN_COMPARATORS`
- `operator` ∈ `symphony_schema._KNOWN_OPERATORS` (`{"any", "all"}`)
- `rebalance` ∈ `symphony_schema.KNOWN_REBALANCE`
- `sort_by_fn` and indicator `fn` values are indicator-fn strings from `symphony_schema.KNOWN_INDICATOR_FNS`
- The `%` placeholder used by `binary_compound` conditions is excluded from the membership-validation walk (`plan_tickers` filters it out)
- `scheme:"market_cap"` is carried in the DSL as a forward-compat token; however, Composer retired market-cap weighting (HTTP 422 `node-type-not-supported`; 2026-06-20). Plans with this scheme are dropped at compile time by `advisors/plan_tree_compiler._has_market_cap` (`reason="market_cap_scheme_deprecated"`). No `make_weight_marketcap` constructor and no `wt-marketcap` in `KNOWN_STEPS` will be added. See `DE-SB-MARKETCAP-DEPRECATED`.

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `N_PLANS_PER_OBJECTIVE` | `12` | Named tunable constant for the default plan count per generation run (AC-10). Pass explicitly to override. |
| `PROVENANCE_BUILT_NEW` | `"built-new"` | Explicit provenance tag stamped on every generator-produced plan as `plan["provenance"]` (AC-13). |
| `PROVENANCE_ATLAS_SUGGESTED` | `"atlas-suggested"` | Explicit provenance tag stamped on every admitted Atlas community candidate in `CandidateInfo.params["provenance"]` (AC-13). |
| `MAX_COMMUNITY_CANDIDATES_PER_RUN` | `20` | Re-exported from `strategy_builder_engine`; single-sourced cap on admitted community candidates. |

### Internal constants (objective-signature lookup tables)

These private frozensets are the single source of truth for `plan_matches_objective` — the generator and its tests both delegate to that function, so these tables cannot drift.

| Name | Values | Used by |
|------|--------|---------|
| `_CONTAINER_KINDS` | `{"group", "weight", "filter", "if", "if_compound"}` | `_diversify_sleeve_count` (counts container-typed direct children) and `_iter_all_nodes` (classifies traversal targets) |
| `_MOMENTUM_QUALITY_SORTS` | `{"cumulative-return", "moving-average-return"}` | `plan_matches_objective` — a `filter` whose `sort_by_fn` is in this set satisfies the `lift_risk_adjusted` signature |
| `_LOW_VOL_SORTS` | `{"max-drawdown", "standard-deviation-return", "standard-deviation-price"}` | `plan_matches_objective` — a `filter` whose `sort_by_fn` is in this set satisfies the `volatility_mitigation` signature |

### Internal constants (prompt-steering)

These are embedded into every SDK prompt by `_build_generation_prompt`.

| Name | Type | Description |
|------|------|-------------|
| `_EXAMPLE_PLAN` | `dict` | A concrete conforming DSL example (diversify-shaped group with two weight sleeves: one `equal`, one `inverse_vol`; tickers SPY/QQQ/TLT/GLD). Derived byte-for-byte from shapes accepted by the C3 compiler. Embedded in every prompt to show Opus the exact field vocabulary without presenting drift tokens as valid. |
| `_EXAMPLE_IF_PLAN` | `dict` | A concrete conforming if-node example (cut_drawdown-shaped; condition is a DICT with `lhs_fn="relative-strength-index"`, `lhs_ticker="SPY"`, `window=10`, `comparator="gt"`, `rhs={"fixed": 80}`; then: equal-weight sleeve; else: inverse_vol sleeve). Verified compiler-clean through `plan_tree_compiler.compile_plan` + `validate_tree==[]`. Embedded in every prompt as the second worked example to teach the nested `condition` dict shape and prevent the "string label" drift pattern. |
| `_EXAMPLE_IF_COMPOUND_PLAN` | `dict` | A concrete conforming if_compound compound-gate plan (cut_drawdown-shaped; condition is `{type:"compound", operator:"all", conditions:[flat-binary(RSI SPY gt 70 w14), binary_compound(max-drawdown QQQ lt 20 w30)]}`; then: equal-weight UVXY/TLT; else: inverse_vol SPY/IEF). **Mixed compound:** contains one `type:"binary"` leaf (flat `lhs_fn`/`lhs_ticker`/`window`, `rhs:{fixed}`) and one `type:"binary_compound"` leaf (`fn`/`tickers`, `rhs:{const}`), so Opus sees both binary sub-shapes inside a compound in a single example. Verified compiler-clean through `plan_tree_compiler.compile_plan` + `validate_tree==[]` using the unified canonical-flat compiler. Embedded in every prompt as the third worked example. (Updated from an all-binary_compound example after the binary-encoding-fix to ensure both sub-shapes are demonstrated.) |
| `_OBJECTIVE_SIGNATURES` | `dict[str, str]` | Per-objective natural-language descriptions of the required AC-8 structural signature, one entry per `Objective` value. Embedded in the prompt for the requested objective to steer Opus toward the correct DSL construct (e.g. `diversify` explicitly states "a lone weight node over N assets is only 1 sleeve and does NOT satisfy the diversify signature"). |

## Public Types

### `Objective` (enum)

Four-value enum defined in this module (independent of `strategy_builder_engine.Objective`, which remains the 3-value enum until the engine rewire in Component 3).

| Value | Description | Structural signature required |
|-------|-------------|-------------------------------|
| `diversify` | Multi-sleeve low-correlation baskets | `>=2` sleeves at the root container |
| `cut_drawdown` | Defensive regime gates + inverse-vol | Regime gate (`if`/`if_compound`) OR `scheme:"inverse_vol"` weight |
| `lift_risk_adjusted` | Momentum / quality tilts | A `filter` whose `sort_by_fn` is a momentum/quality indicator (e.g. `"cumulative-return"`, `"moving-average-return"`). A bare specified-weight basket does NOT satisfy this signature (AC-8 refinement B). |
| `volatility_mitigation` | Low-volatility construction | `scheme:"inverse_vol"` weight OR a `filter` whose `sort_by_fn` is a low/min-vol indicator (e.g. `"max-drawdown"`, `"standard-deviation-return"`, `"standard-deviation-price"`) |

### `GeneratorResult`

Return type of `generate_build_plans`. Never raises.

```python
@dataclass
class GeneratorResult:
    plans: list[dict]   # admitted build-plan dicts; always a list, never None
    reason: str | None  # non-empty when plans == []; None on success
```

### Admitted community candidate shape

`admit_community_candidates` returns `list[CandidateInfo]`. Each `CandidateInfo` carries the provenance tag in `.params["provenance"]`:

```python
CandidateInfo(
    candidate_id = sid,                      # community strategy sid
    tree         = doc["tree"],              # the community strategy tree
    template_id  = "community",
    params       = {
        "sid":              str,
        "name":             str,
        "composition_hash": str,
        "provenance":       "atlas-suggested",  # AC-13: in params, not a top-level dict key
    },
    metrics      = {},
    backtest_error = None,
)
```

`pool_candidates` concatenates built-new `dict` items (from `generate_build_plans`) and `CandidateInfo` items (from `admit_community_candidates`) without reshaping — each item's provenance is accessible as `item["provenance"]` for dicts and `item.params["provenance"]` for `CandidateInfo` objects.

## API Reference

### `_build_client() -> anthropic.Anthropic`

SDK factory seam. Mirrors `ai_advisor._build_client` (`ai_advisor.py:1590`). Reads `ANTHROPIC_API_KEY` from the environment and returns an `anthropic.Anthropic` instance. Raises `RuntimeError` when the key is absent.

**Test seam:** tests patch `advisors.build_plan_generator._build_client`. No live network in the unit suite.

---

### `_build_generation_prompt(objective, n_plans=N_PLANS_PER_OBJECTIVE, membership=None) -> str`

**Prompt-builder seam.** Builds the full SDK prompt for the given objective. Extracted from the old inline f-string to make the sent instructions independently testable without mocking a full SDK round-trip.

The prompt embeds five pieces of steering content:

1. **Full DSL grammar.** The valid `kind` vocabulary (`asset`, `weight`, `group`, `filter`, `if`, `if_compound`) is listed explicitly with the instruction "never use 'weighted' or any other value." The `scheme` field values (`equal`, `specified`, `inverse_vol`) and the `{node, pct}` specified-children shape are taught with a WRONG-vs-CORRECT contrast to prevent the most common Opus drift patterns.
2. **`if`/`if_compound` flat condition sub-grammar.** A dedicated section teaches the flat condition DICT shape for single-condition regime-gate nodes: `lhs_fn`, `lhs_ticker`, `window`, `comparator` (`gt`/`lt`/`gte`/`lte`), `rhs` (`{"fixed": N}` or `{"fn": ..., "ticker": ..., "window": ...}`). A WRONG-vs-CORRECT contrast is included: `WRONG: condition = "spy_above_200d_sma"` / `CORRECT: condition = {...dict...}`. All five fields are stated as required. This section appears in every objective prompt so any objective using an `if` node produces a compilable condition dict.
3. **`if_compound` compound-condition union.** A second condition section teaches the compound-condition shape for multi-condition regime gates: `type` discriminator (`binary`/`binary_compound`/`compound`), `operator` (`any`/`all`), `conditions[]` (list of sub-conditions), `tickers[]` broadcast, `rhs:{const}`. The same WRONG-vs-CORRECT contrast is applied. Embedded in every objective prompt so the full Composer condition grammar is generation-reachable regardless of objective.
4. **Three compiler-verified worked examples.** `_EXAMPLE_PLAN` (diversify-shaped; two weight sleeves), `_EXAMPLE_IF_PLAN` (flat if-node; `rhs: {"fixed": 80}`), and `_EXAMPLE_IF_COMPOUND_PLAN` (mixed compound-gate; `{type:"compound", operator:"all", conditions:[flat-binary leaf, binary_compound leaf]}`) are all embedded verbatim in every prompt. All three have been verified compiler-clean through `plan_tree_compiler.compile_plan` + `validate_tree==[]` using the unified canonical-flat compiler. `_EXAMPLE_IF_COMPOUND_PLAN` is a deliberate **mixed compound** — it contains one `type:"binary"` leaf (flat `lhs_fn`/`lhs_ticker`/`window`, `rhs:{fixed}`) and one `type:"binary_compound"` leaf (`fn`/`tickers`, `rhs:{const}`) so Opus sees both binary sub-shapes inside a single compound example. This was updated after the binary-encoding-fix (Revise-3) from an all-`binary_compound` example that gave Opus no flat-binary model for compound leaves. The full Composer condition grammar — flat `if` and compound `if_compound`, all three condition types, both binary sub-shapes — is now generation-reachable with compiler-verified examples for each construct.
5. **`_OBJECTIVE_SIGNATURES[obj_name]`.** The per-objective structural signature description is embedded for the requested objective, telling Opus which DSL construct is required (e.g. for `lift_risk_adjusted`: "A bare equal-weight basket does NOT satisfy this signature — the filter construct is required").

A sample of up to 20 tickers from `membership` is appended as a universe hint when provided.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `objective` | `Objective` | Steers the structural signature description embedded in the prompt |
| `n_plans` | `int` | Number of plans requested; embedded in the prompt instruction |
| `membership` | `frozenset \| set \| None` | When provided, a sample of up to 20 tickers is appended as a universe hint |

**Returns:** `str` — the full prompt string sent to the SDK `messages.create` call.

**Test seam:** tests can call `_build_generation_prompt(objective)` directly to assert the correct grammar instructions, example, and objective signature are present — without patching the SDK.

---

### `plan_tickers(plan: dict) -> set[str]`

Deterministic walk returning every ticker referenced anywhere in a build-plan.

Reaches into: `asset.ticker`, filter `children` leaves, `if` condition `lhs_ticker` and `rhs.ticker`, `if_compound` condition `lhs.ticker` / `rhs.ticker` / `binary_compound.tickers[]`.

Excludes the `%` binary-compound placeholder. Never raises.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `plan` | `dict` | A top-level build-plan dict conforming to the DSL |

**Returns:** `set[str]` — all ticker strings found in the plan.

---

### `plan_matches_objective(plan: dict, objective) -> bool`

**Public function. Single source of truth for the AC-8 objective-signature admission filter.**

Returns `True` if `plan`'s root structure satisfies the structural signature required by `objective`. Both `generate_build_plans` (the enforcement filter) and the test suite import and call this function directly — tests never reimplement the check, so the filter and assertions cannot drift.

Per-objective logic (all checks are applied anywhere in the tree via `_iter_all_nodes`, except `diversify` which measures direct root children via `_diversify_sleeve_count`):

| Objective | Passes when |
|-----------|-------------|
| `diversify` | `_diversify_sleeve_count(root) >= 2` — root has at least 2 container-typed direct children (sleeves). Asset leaves do not count. |
| `cut_drawdown` | Any node in the tree is an `if`/`if_compound` (regime gate) OR a `weight` with `scheme:"inverse_vol"` |
| `lift_risk_adjusted` | Any `filter` node has `sort_by_fn` in `_MOMENTUM_QUALITY_SORTS`. A bare specified-weight basket returns `False`. |
| `volatility_mitigation` | Any `weight` node has `scheme:"inverse_vol"` OR any `filter` node has `sort_by_fn` in `_LOW_VOL_SORTS` |

Never raises (D-1). Returns `False` for any malformed input or unknown objective.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `plan` | `dict` | A top-level build-plan dict; `plan["root"]` is the traversal entry point |
| `objective` | `Objective` | The objective whose signature to check; also accepts a string matching an `Objective.value` |

**Returns:** `bool`

---

### `generate_build_plans(objective, membership_set, *, n_plans=N_PLANS_PER_OBJECTIVE) -> GeneratorResult`

Generate up to `n_plans` objective-shaped build-plans. Never raises (AC-11).

Calls `_build_client()` → `_build_generation_prompt(objective, n_plans, membership_set)` (embeds DSL grammar + `_EXAMPLE_PLAN` + per-objective signature) → structured tool-use SDK call → parses the `"emit_build_plans"` tool-use block's `.input["plans"]` list → membership-validates every ticker → deduplicates structurally-identical plans → enforces objective structural signature → returns admitted plans tagged `provenance="built-new"`.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `objective` | `Objective` | Steers the structural shape constraint applied to each plan |
| `membership_set` | `frozenset[str] \| set[str]` | The tradeable universe; typically `universe_provider.get_tradeable_set()`. Every ticker in every plan is checked against this set. |
| `n_plans` | `int` | Number of plans to request. Defaults to `N_PLANS_PER_OBJECTIVE` (12). |

**Returns:** `GeneratorResult`

**SDK call budget (DE-SB-GEN-TRUNCATION):**

Generating `N_PLANS_PER_OBJECTIVE=12` full-grammar build-plans requires substantially more than 4096 output tokens. The original `max_tokens=4096` literal truncated the JSON mid-payload, causing `tool_block.input.get("plans")` to return `{}` (`input_json_chars=2`) — a non-list value that hit the `InvalidToolUsePayload` degradation path and returned 0 plans. This was non-deterministic: which objectives truncated depended on plan complexity and token packing for that run (~3/4 objectives affected per run in live diagnosis).

Fix: two changes in `advisors/build_plan_generator.py` (GREEN 2a1787e):

- **`MAX_OUTPUT_TOKENS = 16384`** replaces the bare `4096` literal. Empirical calibration (2026-06-20, non-truncated run at `max_tokens=32000`): worst-case = 5,015 output tokens (`diversify` objective). 16,384 is ~3.3x that ceiling — generous headroom, robust to Opus output variance. `max_tokens` is a billing ceiling, not a billed quantity; a generous value carries no cost penalty.
- **`MAX_GENERATION_ATTEMPTS = 3` bounded retry loop.** A `for _attempt in range(MAX_GENERATION_ATTEMPTS)` loop wraps the `messages.create` call. After each response, `stop_reason` is inspected: anything other than `"max_tokens"` breaks the loop and proceeds to parsing. A `"max_tokens"` response logs a warning and retries. After all attempts are exhausted, the loop's `else` clause returns `GeneratorResult(plans=[], reason="max_tokens: response truncated after all attempts")` — honest D-1 degradation, never a raise.

See `DE-SB-GEN-TRUNCATION` in `DECISIONS.md`.

**Admission pipeline order (fixed — AC-8 enforcement test pins this):**

For each raw plan from the SDK response, the following steps are applied in strict order. A plan is dropped at the first failing step; the run continues with the remaining plans.

1. **Membership prune (AC-9):** `_validate_and_prune` checks every ticker. Off-universe tickers in sibling-safe nodes are pruned; degenerate/empty nodes or unpreable condition references reject the whole plan.
2. **Provenance tag (AC-13):** `plan["provenance"] = "built-new"` stamped unconditionally on surviving plans.
3. **Structural dedup (AC-10):** `_root_fingerprint` computes `sha256(json.dumps(plan["root"], sort_keys=True))`; plans with a seen fingerprint are dropped.
4. **Objective signature filter (AC-8 B):** `plan_matches_objective(pruned, objective)` is called; plans failing the signature are dropped. This runs AFTER prune+dedup so a plan whose structure degrades below the threshold after pruning is correctly rejected here, not silently admitted.

**Empty-result reason (AC-8 B + AC-11/AC-23):** If the signature filter eliminates all remaining plans (leaving `admitted == []`), `GeneratorResult` carries `reason=f"no plans matched the {obj_name} signature after prune and dedup"` — never `reason=None` (which would look like a parse failure rather than a signature-floor result).

**Membership validation detail (AC-9 — refinement A):**
- If a plan contains an off-universe ticker AND in-universe siblings remain in the same node, the off-universe ticker is pruned.
- If pruning would leave a node empty or degenerate (no children), the entire plan is rejected — never emitted broken.
- Off-universe tickers in `if`/`if_compound` condition signal references are not prunable; the plan is rejected outright.
- An empty `membership_set` causes every ticker to be off-universe; all plans are rejected and `plans == []`.
- Admitted plans are returned as `copy.deepcopy` objects — no node aliases the SDK response. This is required because Component 3 mutates admitted trees during compilation (the same reason `symphony_schema` constructors deep-copy their children).

**Structural deduplication detail (AC-10 — refinement C):** Plans are fingerprinted via `_root_fingerprint`, which computes `sha256(json.dumps(plan["root"], sort_keys=True))` over the `root` NODE only. Volatile top-level fields (`plan_id`, `name`, `provenance`) are excluded by operating only on the root subtree. Structurally-identical plans (same shape + tickers, differing only in `plan_id`/`name`) are collapsed to one representative. Admitted count is always `<= n_plans`.

**Degradation paths (AC-11):**
- `_build_client()` raises (missing key) → `GeneratorResult(plans=[], reason="RuntimeError")`
- SDK `messages.create` raises → `GeneratorResult(plans=[], reason=type(exc).__name__)`
- Tool-use block absent → `GeneratorResult(plans=[], reason="NoToolUseBlock")`
- `plans` payload is not a list → `GeneratorResult(plans=[], reason="InvalidToolUsePayload")`
- All plans filtered by signature (after prune and dedup) → `GeneratorResult(plans=[], reason="no plans matched the <obj> signature after prune and dedup")`
- All plans rejected by membership validation or dedup only → `GeneratorResult(plans=[], reason=None)` (empty plans without a signature-filter cause is not itself an error)

`reason` contains ONLY `type(exc).__name__` for exceptions — never the API key, a file path, or any exception message body (D-1 contract).

**Example:**
```python
from advisors.build_plan_generator import generate_build_plans, Objective
from advisors.universe_provider import get_tradeable_set

membership = get_tradeable_set()
result = generate_build_plans(Objective.cut_drawdown, membership)
if result.plans:
    for plan in result.plans:
        print(plan["name"], plan["provenance"])
else:
    print("generation failed:", result.reason)
```

---

### `admit_community_candidates(community_result, objective, *, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN) -> list`

Rank and admit Atlas community strategies by objective-relevance (AC-12). Never raises.

Takes the dict returned by `advisors.community_strats.load_community_strategies` and returns a list of `CandidateInfo` objects ranked by the objective's named stat, each tagged `provenance="atlas-suggested"` in `.params["provenance"]`.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `community_result` | `dict` | Return value of `load_community_strategies`. When `available` is `False`, or the input is not a dict, or `candidates` is missing/not a list, returns `[]`. |
| `objective` | `Objective` | Determines the ranking stat |
| `max_candidates` | `int` | Hard cap. Defaults to `MAX_COMMUNITY_CANDIDATES_PER_RUN` (20). |

**Returns:** `list[CandidateInfo]` — admitted community candidates. Empty list on any failure.

**Ranking per objective (AC-12):**

| Objective | Stat key | Direction | Notes |
|-----------|----------|-----------|-------|
| `cut_drawdown` | `oos_metrics["max_drawdown"]` | Nearer zero first (shallowest drawdown) | quantstats convention: values are <= 0 |
| `volatility_mitigation` | `oos_metrics["volatility"]` | Lowest first | |
| `lift_risk_adjusted` | `oos_metrics["sharpe"]` | Highest first | |
| `diversify` | Jaccard overlap vs already-admitted ticker set | Lowest overlap first (greedy) | Tiebreaks by `sid` sort; deterministic; complete set up to cap |

**Missing-stat handling (AC-12 — PM-decided: KEPT-LAST):** A doc whose `oos_metrics` is `None`, lacks the key, or has a non-numeric value for the stat is admitted AFTER all docs that have a valid numeric stat — never pre-dropped. The FDR gate, PBO veto, and SPY-OOS baseline in the downstream pipeline are the real survival gates.

---

### `load_atlas_candidates(objective, *, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN) -> list`

Convenience wrapper: calls `advisors.community_strats.load_community_strategies(force_refresh=False)` then `admit_community_candidates`. Enforces the weekly-cache / bill-protection directive (`force_refresh=False` is mandatory — never a per-request forced refetch). Never raises.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `objective` | `Objective` | Passed through to `admit_community_candidates` |
| `max_candidates` | `int` | Hard cap; defaults to `MAX_COMMUNITY_CANDIDATES_PER_RUN` (20) |

**Returns:** `list[CandidateInfo]` — same shape as `admit_community_candidates`.

---

### `pool_candidates(built_new: list, atlas_suggested: list) -> list`

Pool the two provenance sources into one list for the downstream FDR gate (AC-13 C2/2b slice). Never raises.

Concatenates both lists without reshaping. `built_new` items are `dict` objects (provenance at `item["provenance"]`); `atlas_suggested` items are `CandidateInfo` objects (provenance at `item.params["provenance"]`). Each item's existing provenance tag is preserved unchanged.

The resulting pooled list is the future input to `strategy_builder_engine.propose_strategies`'s single-batch FDR gate (wiring deferred to Component 3/5 — see Forward-AC below).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `built_new` | `list[dict]` | Plans from `generate_build_plans` (each carries `plan["provenance"]="built-new"`) |
| `atlas_suggested` | `list[CandidateInfo]` | Candidates from `admit_community_candidates` (each carries `.params["provenance"]="atlas-suggested"`) |

**Returns:** `list` — pooled candidates; `len(result) == len(built_new) + len(atlas_suggested)`.

## Forward-AC: C3/C5 boundary

The following AC-13 behaviors are DEFERRED to later phases on this same branch and are NOT yet implemented:

- Both provenance sources entering the SAME single-batch `evaluate_candidate_batch` FDR gate (Component 3 / compiler + engine rewire integration)
- Gate count including both sources
- Provenance tag surviving to the persisted `advisor_observations.raw_response`
- Provenance tag surfacing in the SPA route JSON (Component 5 / route rewire)

These are forward-ACs; the C2/2b module provides the pooled list and provenance tags; the wiring into the gate and persistence is Component 3/5 work.

## Internal Dependencies

- `advisors.community_strats` — `load_community_strategies` (called inside `load_atlas_candidates`, CC-2 lazy import)
- `advisors.strategy_builder_engine` — `MAX_COMMUNITY_CANDIDATES_PER_RUN` and `CandidateInfo` re-exported (the engine itself is NOT modified in this phase)
- `anthropic` — SDK client via `_build_client()` (lazy import inside the function, CC-2 boundary)
- `advisors.symphony_schema` — `KNOWN_COMPARATORS`, `KNOWN_REBALANCE`, `_KNOWN_OPERATORS` (vocabulary constants for DSL validation)

No imports from `database`, `autotuner`, `app`, or any execution module. Off-execution-path; advisory-only.

## Design Notes

- **DSL-not-raw-JSON.** Opus emits a strategy DSL, not a Composer `raw_value` tree. This decouples generation from compilation: the generator can be tested against the DSL contract without a live Composer endpoint; the compiler (C3) is a pure dispatch table; and the DSL makes the generator↔compiler contract legible and auditable. Prompt-injection is also bounded: a DSL node with an unexpected `kind` or `scheme` fails DSL shape validation before reaching the compiler.
- **Structured tool-use with tightened schema (defense-in-depth).** The Anthropic SDK call uses tool-use mode (`"emit_build_plans"` tool, `tool_choice={"type":"tool","name":"emit_build_plans"}`). The `_EMIT_BUILD_PLANS_TOOL` input schema has been extended in two phases:
  - **C2-fix (initial):** `NODE.kind` restricted to `{"asset","weight","group","filter","if","if_compound"}` (excludes drift value `"weighted"`); `weight.scheme` restricted to `{"equal","specified","inverse_vol"}`; plan-level fields typed and `required`; `rebalance` enum-constrained.
  - **C2-fix Revise-1 (flat condition sub-grammar):** Added a `condition` property to the `if`/`if_compound` node entry: typed `object` with properties `lhs_fn` (string), `lhs_ticker` (string), `window` (integer), `comparator` (enum: `["gt","lt","gte","lte"]`), `rhs` (object); all five fields `required`. Structurally prevents Opus from emitting a bare string condition (the Revise-1 drift pattern).
  - **C2-fix Revise-2 (compound-condition union, CLOSED):** The `condition` property is extended with the compound-union fields: `type` (enum: `["binary","binary_compound","compound"]`), `operator` (enum: `["any","all"]`), `conditions` (array), `tickers` (array), `fn` (string). The `condition` property remains `object`-typed (the Revise-1 no-string invariant is preserved). This closes the `if_compound` compound-condition generation gap: the full Composer condition grammar (`binary`, `binary_compound`, `compound` discriminators) is now schema-constrained as well as prompt-taught.
  This schema tightening cannot prevent all deep-nesting drift (the JSON schema is not recursive), but it forces the correct top-level tokens and field vocabulary across the full condition grammar. The response is a structured `tool_use` block; free-text responses (`stop_reason="end_turn"`) are treated as failures and degrade to empty plans.
- **Two test seams: `_build_client` (SDK) and `_build_generation_prompt` (prompt content).** `_build_client` is a module-level callable patched in tests to intercept the SDK call entirely — the same pattern used by `ai_advisor.py:1590`. `_build_generation_prompt` is a standalone function that tests can call directly to assert the correct grammar instructions, `_EXAMPLE_PLAN`, and per-objective signature text are present in the prompt, without a full SDK mock. No live network is required in the unit suite.
- **`plan_matches_objective` is the single source of truth for AC-8.** The enforcement filter in `generate_build_plans` and all test assertions that check objective structural compliance both call this public function. Neither reimplements the signature logic, so they cannot drift. The filter runs after prune+dedup — order pinned by AC-8 enforcement tests.
- **Deep-copy on admission (AC-9 no-alias guarantee).** `_validate_and_prune` returns a `copy.deepcopy` of every admitted plan so no node aliases the SDK response object. This is required because the Component 3 compiler mutates tree nodes during compilation (the same reason `symphony_schema` constructors deep-copy their children).
- **`plan_tickers` / `_collect_condition_tickers` reads canonical-flat binary leaf operands (AC-9 generator-walker fix, 2026-06-20).** The membership validator (AC-9) uses `plan_tickers` to collect all tickers a plan references so off-universe operands can be pruned before the plan is admitted. `_collect_condition_tickers` — the internal helper that descends into `condition` blocks — previously read binary leaf operands with the nested shape `cond["lhs"]["ticker"]`. After the binary-encoding-fix unified the binary contract onto flat fields, this left the generator-side ticker walker BLIND to a flat binary leaf's `lhs_ticker` inside a compound: an off-universe lhs operand (e.g. gating on RSI of a delisted symbol) would slip membership validation un-pruned and reach the compiler and backtest. Fixed: `_collect_condition_tickers` binary branch now reads `cond.get("lhs_ticker")` (canonical-flat) and collects the ticker-comparison rhs ticker when present, preserving the `%` skip and leaving `binary_compound`/`compound` branches unchanged. Both ticker-walking paths are now consistent on the canonical-flat encoding: PATH A (`plan_tickers` via `_collect_condition_tickers` — generator membership-prune, AC-9) and PATH B (`symphony_schema.extract_tickers` — compiler repair-prune, AC-16). The AC-9 escape is closed: a plan with an off-universe lhs operand in a compound binary leaf is now rejected at membership validation, not silently admitted.
- **Robustness: unknown-kind nodes are rejected, not passed through.** Prior to the C2-fix, `_prune_node` passed unknown `kind` values through unchanged (to future-proof unknown node types). A live Opus run proved this was wrong: Opus emitted `kind:"weighted"` (a drift token absent from the DSL) which passed through `_prune_node` silently, survived to `plan_tickers()` with zero extractable tickers, and was blocked only by the downstream AC-8 signature filter — leaving 0 admitted plans across all 4 objectives. Fix: `_prune_node` now returns `None` for any unknown `kind`, rejecting the plan. Additionally, `_validate_and_prune` adds a post-prune zero-ticker check (`if not plan_tickers(validated): return None`) to catch any nested-unknown-kind case where an inner unknown-kind node is wrapped by a known outer kind and survives `_prune_node`.
- **Structural dedup fingerprints the root node, not the full plan.** `_root_fingerprint` computes `sha256(json.dumps(plan["root"], sort_keys=True))` over the `root` NODE. Volatile top-level fields (`plan_id`, `name`, `provenance`) are excluded by operating only on the subtree — two plans with identical structure but different names hash identically.
- **D-1 error contract.** `reason` strings contain `type(exc).__name__` only. No API key value, no file path, no exception message body ever appears in a returned reason string.
- **Bill-protection on Atlas pulls.** `load_atlas_candidates` passes `force_refresh=False` unconditionally — Atlas reads are bounded to at most once per week per the operator directive (see `DE-ATLAS-001`).
- **Heterogeneous pool.** `pool_candidates` returns a mixed-type list: `dict` items (built-new plans) and `CandidateInfo` items (atlas-suggested). The downstream FDR gate in `strategy_builder_engine.evaluate_candidate_batch` operates on `CandidateInfo` objects; the Component 3 engine rewire will normalize built-new dicts into `CandidateInfo` before calling the gate (deferred to C3).
- **Independent `Objective` enum.** This module defines its own 4-value `Objective` enum. `strategy_builder_engine.Objective` remains the 3-value enum until Component 3 unifies them during the engine rewire.
- **`market_cap` scheme is a forward-compat DSL token; the constructor was never added.** Composer retired market-cap weighting (HTTP 422 `node-type-not-supported` / "Market cap weighting is no longer supported"; captured 2026-06-20; evidence at `tests/fixtures/strategy_builder/wt_marketcap_deprecated_envelope.json`). Per PM Option A (adopt-the-provider-contract), no `make_weight_marketcap` constructor and no `wt-marketcap` entry in `KNOWN_STEPS` are added. The DSL retains `scheme:"market_cap"` as a recognized value so generator plans are structurally valid; `advisors/plan_tree_compiler._has_market_cap` detects and drops them before compilation. See `DE-SB-MARKETCAP-DEPRECATED`.
