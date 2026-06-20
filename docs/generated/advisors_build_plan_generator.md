# advisors/build_plan_generator

> Opus Build-Plan Generator for the real Strategy Builder (Component 2 + 2b): uses the Anthropic SDK in structured tool-use mode to emit diverse objective-shaped build-plans expressed in a constrained strategy DSL, validates every proposed ticker against the tradeable membership set, and admits objective-matched Atlas community strategies alongside the generated plans.

**Source:** `advisors/build_plan_generator.py`
**Last updated:** 2026-06-20

## Overview

`advisors/build_plan_generator.py` is the Opus-backed brain of the real Strategy Builder. It produces the plans that the Component 3 compiler translates into Composer trees, replacing the 7-template stamper in `_generate_candidate_trees` (the engine rewire happens in Component 3).

The module has two responsibilities:

1. **Build-plan generation (AC-7..AC-11).** It calls the Anthropic SDK (`ANTHROPIC_API_KEY`) in structured tool-use mode and receives up to `N_PLANS_PER_OBJECTIVE` diverse build-plans expressed in the build-plan DSL — a JSON-serializable, constraint-typed intermediate representation. It is NOT raw Composer JSON; it is a 1:1 pre-image of the `symphony_schema` constructor API so the compiler in Component 3 is a pure dispatch table with no interpretation. Opus proposes tickers from its own market knowledge; every referenced ticker is then validated against the caller-supplied `membership_set` (a `frozenset` from `universe_provider.get_tradeable_set()`) before a plan is admitted.

2. **Objective-matched Atlas admission (AC-12..AC-13).** It ranks and admits existing Atlas community strategies by objective-relevance (not as an unfiltered top-20), tags them with explicit provenance in `.params["provenance"]`, and pools them with the generated plans for the single downstream FDR gate.

Off-execution-path. Advisory-only. Never raises — every function degrades honestly on failure.

**Scope note:** `advisors/strategy_builder_engine.py` and `advisors/community_strats.py` are NOT modified in this phase. The engine's existing 3-value `Objective` enum stays in place; this module defines its own independent 4-value `Objective`. The engine rewire (_generate_candidate_trees replacement) and community_strats changes land in Component 3.

## The Build-Plan DSL (canonical generator↔compiler contract)

A build-plan is a JSON-serializable `dict`. The generator produces these; the compiler (Component 3, `advisors/plan_compiler.py`) translates each into a valid Composer `raw_value` tree via `symphony_schema` constructors. The DSL is a thin 1:1 pre-image of the constructor API — each `kind`/`scheme` maps to exactly one constructor.

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
| `{kind:"weight", scheme:"market_cap", children:[NODE...]}` | `make_weight_marketcap` | Constructor lands in Component 3 (AC-17); DSL carries it now as forward-compat |
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
- `scheme:"market_cap"` is carried in the DSL now as forward-compatibility; `make_weight_marketcap` in `symphony_schema` lands in Component 3 (AC-17)

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `N_PLANS_PER_OBJECTIVE` | `12` | Named tunable constant for the default plan count per generation run (AC-10). Pass explicitly to override. |
| `PROVENANCE_BUILT_NEW` | `"built-new"` | Explicit provenance tag stamped on every generator-produced plan as `plan["provenance"]` (AC-13). |
| `PROVENANCE_ATLAS_SUGGESTED` | `"atlas-suggested"` | Explicit provenance tag stamped on every admitted Atlas community candidate in `CandidateInfo.params["provenance"]` (AC-13). |
| `MAX_COMMUNITY_CANDIDATES_PER_RUN` | `20` | Re-exported from `strategy_builder_engine`; single-sourced cap on admitted community candidates. |

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

### `generate_build_plans(objective, membership_set, *, n_plans=N_PLANS_PER_OBJECTIVE) -> GeneratorResult`

Generate up to `n_plans` objective-shaped build-plans. Never raises (AC-11).

Calls `_build_client()` → structured tool-use SDK call → parses the `"emit_build_plans"` tool-use block's `.input["plans"]` list → membership-validates every ticker → enforces objective structural signature → deduplicates structurally-identical plans → returns admitted plans tagged `provenance="built-new"`.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `objective` | `Objective` | Steers the structural shape constraint applied to each plan |
| `membership_set` | `frozenset[str] \| set[str]` | The tradeable universe; typically `universe_provider.get_tradeable_set()`. Every ticker in every plan is checked against this set. |
| `n_plans` | `int` | Number of plans to request. Defaults to `N_PLANS_PER_OBJECTIVE` (12). |

**Returns:** `GeneratorResult`

**Membership validation (AC-9 — refinement A):**
- If a plan contains an off-universe ticker AND in-universe siblings remain in the same node, the off-universe ticker is pruned.
- If pruning would leave a node empty or degenerate (no children), the entire plan is rejected — never emitted broken.
- Off-universe tickers in `if`/`if_compound` condition signal references are not prunable; the plan is rejected outright.
- An empty `membership_set` causes every ticker to be off-universe; all plans are rejected and `plans == []`.
- Admitted plans are returned as `copy.deepcopy` objects — no node aliases the SDK response. This is required because Component 3 mutates admitted trees during compilation (the same reason `symphony_schema` constructors deep-copy their children).

**Objective structural enforcement (AC-8):** Each plan is validated against the objective's structural signature after membership validation. Plans failing the signature are dropped before admission. The four signatures are mutually distinguishable.

**Structural deduplication (AC-10 — refinement C):** Plans are fingerprinted via `_root_fingerprint`, which computes `sha256(json.dumps(plan["root"], sort_keys=True))` over the `root` NODE only. Volatile top-level fields (`plan_id`, `name`, `provenance`) are excluded by operating only on the root subtree. Structurally-identical plans (same shape + tickers, differing only in `plan_id`/`name`) are collapsed to one representative. Admitted count is always `<= n_plans`.

**Degradation paths (AC-11):**
- `_build_client()` raises (missing key) → `GeneratorResult(plans=[], reason="RuntimeError")`
- SDK `messages.create` raises → `GeneratorResult(plans=[], reason=type(exc).__name__)`
- Tool-use block absent → `GeneratorResult(plans=[], reason="NoToolUseBlock")`
- `plans` payload is not a list → `GeneratorResult(plans=[], reason="InvalidToolUsePayload")`
- All plans rejected by membership validation, signature check, or dedup → `GeneratorResult(plans=[], reason=None)` (empty plans is not itself an error)

`reason` contains ONLY `type(exc).__name__` — never the API key, a file path, or any exception message body (D-1 contract).

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
- **Structured tool-use, not free text.** The Anthropic SDK call uses tool-use mode (`"emit_build_plans"` tool with a JSON schema, `tool_choice={"type":"tool","name":"emit_build_plans"}`). The response is a structured `tool_use` block; free-text responses (`stop_reason="end_turn"`) are treated as failures and degrade to empty plans.
- **SDK seam mirrors `ai_advisor._build_client`.** The `_build_client` factory is a module-level callable patched in tests (`advisors.build_plan_generator._build_client`) — the same pattern used by `ai_advisor.py:1590`. No live network is required in the test suite.
- **Deep-copy on admission (AC-9 no-alias guarantee).** `_validate_and_prune` returns a `copy.deepcopy` of every admitted plan so no node aliases the SDK response object. This is required because the Component 3 compiler mutates tree nodes during compilation (the same reason `symphony_schema` constructors deep-copy their children).
- **Structural dedup fingerprints the root node, not the full plan.** `_root_fingerprint` computes `sha256(json.dumps(plan["root"], sort_keys=True))` over the `root` NODE. Volatile top-level fields (`plan_id`, `name`, `provenance`) are excluded by operating only on the subtree — two plans with identical structure but different names hash identically.
- **D-1 error contract.** `reason` strings contain `type(exc).__name__` only. No API key value, no file path, no exception message body ever appears in a returned reason string.
- **Bill-protection on Atlas pulls.** `load_atlas_candidates` passes `force_refresh=False` unconditionally — Atlas reads are bounded to at most once per week per the operator directive (see `DE-ATLAS-001`).
- **Heterogeneous pool.** `pool_candidates` returns a mixed-type list: `dict` items (built-new plans) and `CandidateInfo` items (atlas-suggested). The downstream FDR gate in `strategy_builder_engine.evaluate_candidate_batch` operates on `CandidateInfo` objects; the Component 3 engine rewire will normalize built-new dicts into `CandidateInfo` before calling the gate (deferred to C3).
- **Independent `Objective` enum.** This module defines its own 4-value `Objective` enum. `strategy_builder_engine.Objective` remains the 3-value enum until Component 3 unifies them during the engine rewire.
- **`market_cap` scheme is forward-compat.** The DSL carries `scheme:"market_cap"` now so plans involving market-cap weighting can be generated; `make_weight_marketcap` in `symphony_schema` and the `KNOWN_STEPS` entry land in Component 3 (AC-17). A compiler receiving a `market_cap` plan node before C3 ships will error at compile time, not at generation time.
