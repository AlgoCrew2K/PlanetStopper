"""RED tests — C2 generator DSL-conformance fix (advisors/build_plan_generator.py).

WHY THIS FILE EXISTS (the tests-green-but-hollow trap): the 47 existing C2 tests
mock the Anthropic SDK, so they never exercise what REAL Opus emits. A live exam
(real Opus + the real 12,748-symbol universe) found generate_build_plans returns
0 admitted plans for ALL FOUR objectives — real Opus emits structurally-sensible
strategies in the WRONG DSL VOCABULARY (root kind:"weighted" instead of
kind:"weight"+scheme; specified children {kind:"node","weight":0.4,"node":{...}}
instead of {node, pct}; assets carry a bare "weight" float). Every such plan walks
to plan_tickers()==set() and plan_matches_objective()==False, so all drop.

ROOT CAUSE: the generator prompt references "the approved build-plan DSL" but never
GIVES Opus the grammar, and _EMIT_BUILD_PLANS_TOOL is too loose to constrain the
kind/scheme tokens. The AC-8 enforcement filter is the GUARANTEE; the missing piece
is prompt+schema STEERING toward the right vocabulary.

THE FIX UNDER TEST (prompt-STEER + enforce-GUARANTEE):
  1. Embed the EXACT build-plan DSL grammar in the generator prompt (kind/scheme
     vocabulary, the {node,pct} specified shape, the scheme field) + a CONCRETE
     conforming example.
  2. Describe each objective's structural SIGNATURE in the prompt.
  3. TIGHTEN _EMIT_BUILD_PLANS_TOOL to enum-constrain kind/scheme + child shapes.
  4. KEEP the AC-8 enforcement filter + prune/dedup order (the existing 47 tests).
  + Robustness: a plan with an UNKNOWN container kind / 0 extractable tickers is
    REJECTED at _validate_and_prune, not silently surviving to the signature filter.

TESTABLE SURFACES (the SDK is mocked — we cannot see live drift in a unit test, so
we test the INSTRUCTIONS WE SEND + the pipeline's admission/rejection behaviour):
  - the extracted prompt-builder seam (impl extracts it; tests pin BEHAVIOUR, not
    the function name — resolved via a small set of accepted names).
  - the tightened _EMIT_BUILD_PLANS_TOOL schema (walked structurally for enum
    constraints — NOT brittle string matching).
  - generate_build_plans with a CONFORMING mocked response (admits >=1 plan/obj).
  - _validate_and_prune on a drift-vocabulary plan (rejects, returns None).

ADVERSARIAL FOCUS: the embedded example must ITSELF be valid DSL (plan_tickers>0 +
plan_matches_objective) — that is what stops a fix from teaching the WRONG grammar.
The schema assertions walk the dict for enum constraints. No producer values are
asserted; the math/schema engine is never mocked.
"""

from __future__ import annotations

import json

import pytest

from advisors import symphony_schema

# ---------------------------------------------------------------------------
# Module-under-test import guard.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bpg():
    import advisors.build_plan_generator as _bpg  # noqa: PLC0415

    return _bpg


_OBJECTIVE_NAMES = ("diversify", "cut_drawdown", "lift_risk_adjusted", "volatility_mitigation")


def _objective(bpg, name: str):
    return bpg.Objective[name]


# ---------------------------------------------------------------------------
# Prompt-builder seam resolver. The implementer EXTRACTS the inline prompt into a
# testable builder; the exact name is impl's choice, so we resolve it from a small
# set of accepted names. The test pins BEHAVIOUR (what the prompt contains), not
# the name. If none is found, the seam does not exist yet -> RED (the fix must
# extract it).
# ---------------------------------------------------------------------------

_ACCEPTED_PROMPT_BUILDERS = (
    "_build_generation_prompt",
    "_build_prompt",
    "_generation_prompt",
    "build_generation_prompt",
)


def _resolve_prompt_builder(bpg):
    for name in _ACCEPTED_PROMPT_BUILDERS:
        fn = getattr(bpg, name, None)
        if callable(fn):
            return fn
    raise AttributeError(
        "no prompt-builder seam found on build_plan_generator; the fix must extract "
        f"the inline prompt into one of: {_ACCEPTED_PROMPT_BUILDERS}"
    )


def _build_prompt(bpg, objective, *, n_plans=12, membership=None):
    """Call the resolved prompt-builder with the broadest plausible signature.

    The builder takes (objective, n_plans, membership) in some order; we try the
    most likely call shapes so the test pins behaviour, not the exact signature.
    """
    fn = _resolve_prompt_builder(bpg)
    membership = membership if membership is not None else frozenset({"SPY", "QQQ", "TLT"})
    # Try keyword-rich call first, then positional fallbacks.
    for attempt in (
        lambda: fn(objective, n_plans=n_plans, membership=membership),
        lambda: fn(objective, membership, n_plans=n_plans),
        lambda: fn(objective, n_plans, membership),
        lambda: fn(objective, n_plans),
        lambda: fn(objective),
    ):
        try:
            result = attempt()
            if isinstance(result, str):
                return result
        except TypeError:
            continue
    raise AssertionError("prompt-builder did not return a str for any accepted call shape")


# ---------------------------------------------------------------------------
# DSL example extraction. The fix embeds a CONCRETE conforming example plan inside
# the prompt. We locate a fenced/JSON-ish build-plan object in the prompt text and
# parse it so we can prove it is VALID DSL (plan_tickers>0 + plan_matches_objective).
# ---------------------------------------------------------------------------


def _extract_example_plan(prompt: str) -> dict | None:
    """Best-effort: find a JSON object in the prompt that has a 'root' with a 'kind'.

    Scans for balanced-brace JSON objects and returns the first one that parses and
    looks like a build-plan (has a 'root' dict carrying a 'kind'). Returns None if
    none found — the test then fails (the example is required).
    """
    candidates = _find_json_objects(prompt)
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        root = obj.get("root")
        if isinstance(root, dict) and "kind" in root:
            return obj
        # Some prompts may embed the NODE directly as the example root.
        if obj.get("kind") in {"weight", "group", "filter", "if", "if_compound"}:
            return {
                "plan_id": "ex",
                "objective": "diversify",
                "name": "ex",
                "rebalance": "daily",
                "root": obj,
            }
    return None


def _find_json_objects(text: str) -> list:
    """Yield every top-level balanced-brace JSON object that parses from text."""
    objs = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    chunk = text[start : i + 1]
                    try:
                        objs.append(json.loads(chunk))
                    except (ValueError, json.JSONDecodeError):
                        pass
                    start = -1
    return objs


# ---------------------------------------------------------------------------
# Conforming-mock SDK plumbing (mirrors the existing C2 test conventions).
# ---------------------------------------------------------------------------


def _asset(t):
    return {"kind": "asset", "ticker": t}


def _equal(children):
    return {"kind": "weight", "scheme": "equal", "children": children}


def _group(name, children):
    return {"kind": "group", "name": name, "children": children}


def _inverse_vol(children):
    return {"kind": "weight", "scheme": "inverse_vol", "children": children}


def _filter(sort_by_fn, children, *, select_fn="top", select_n=2, window=63):
    return {
        "kind": "filter",
        "select_fn": select_fn,
        "select_n": select_n,
        "sort_by_fn": sort_by_fn,
        "window": window,
        "children": children,
    }


def _plan(objective, root, *, plan_id="p", name="Plan"):
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": name,
        "rebalance": "daily",
        "root": root,
    }


def _conforming_root_for(objective_name: str) -> dict:
    """A VALID-DSL root satisfying objective_name's AC-8 structural signature.

    Derived from the same DSL shapes the C3 golden fixtures / compiler accept —
    so a plan built here both compiles AND matches the objective predicate.
    """
    if objective_name == "diversify":
        # >=2 allocation-container sleeves.
        return _group(
            "port",
            [
                _equal([_asset("SPY"), _asset("QQQ")]),
                _equal([_asset("TLT"), _asset("GLD")]),
            ],
        )
    if objective_name == "cut_drawdown":
        # inverse-vol sleeve satisfies the regime-gate-OR-inverse-vol signature.
        return _inverse_vol([_asset("SPY"), _asset("TLT")])
    if objective_name == "lift_risk_adjusted":
        # momentum/quality FILTER (sort_by_fn a momentum indicator).
        return _filter("cumulative-return", [_asset("AAPL"), _asset("MSFT"), _asset("NVDA")])
    if objective_name == "volatility_mitigation":
        # low/min-vol FILTER (sort_by_fn a vol indicator).
        return _filter("standard-deviation-return", [_asset("SPY"), _asset("TLT"), _asset("IEF")])
    raise ValueError(objective_name)


class _MockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload
        self.name = "emit_build_plans"


class _MockResponse:
    def __init__(self, plans):
        self.stop_reason = "tool_use"
        self.content = [_MockBlock({"plans": plans})]


class _MockClient:
    def __init__(self, plans):
        self._plans = plans

    class _Messages:
        def __init__(self, plans):
            self._plans = plans

        def create(self, *a, **k):
            return _MockResponse(self._plans)

    @property
    def messages(self):
        return self._Messages(self._plans)


def _patch_conforming_client(bpg, monkeypatch, plans):
    monkeypatch.setattr(bpg, "_build_client", lambda: _MockClient(plans))


# ===========================================================================
# GROUP 1 — the prompt carries the DSL grammar (the steering fix).
# ===========================================================================


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_prompt_contains_kind_vocabulary_for_every_objective(bpg, obj_name):
    """The prompt for each objective names the valid NODE kind vocabulary so Opus
    emits the right tokens (asset/weight/group/filter/if). Without this Opus drifts
    to 'weighted'/'node' (the live-exam failure)."""
    prompt = _build_prompt(bpg, _objective(bpg, obj_name))
    for kind in ("asset", "weight", "group", "filter"):
        assert kind in prompt, f"prompt for {obj_name} missing kind token {kind!r}"


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_prompt_contains_scheme_vocabulary(bpg, obj_name):
    """The prompt names the weight `scheme` field + its values (equal/specified/
    inverse_vol) — the missing 'scheme' field is exactly what drove the drift to a
    bare kind:'weighted'."""
    prompt = _build_prompt(bpg, _objective(bpg, obj_name))
    assert "scheme" in prompt, "prompt must name the weight 'scheme' field"
    for scheme in ("equal", "specified", "inverse_vol"):
        assert scheme in prompt, f"prompt missing scheme value {scheme!r}"


def test_prompt_names_specified_children_node_pct_shape(bpg):
    """The prompt teaches the {node, pct} specified-children shape — NOT a bare
    weight float. Real Opus emitted {kind:'node','weight':0.4}; the prompt must
    teach the shape plan_tickers actually walks."""
    prompt = _build_prompt(bpg, _objective(bpg, "diversify"))
    # Both field names of the specified-children entry must appear.
    assert "pct" in prompt, "prompt must teach the specified-children 'pct' field"
    assert "node" in prompt, "prompt must teach the specified-children 'node' field"


def test_prompt_embeds_a_conforming_example_that_parses_valid(bpg):
    """ADVERSARIAL CORE: the prompt embeds a CONCRETE example plan, and that example
    must ITSELF be valid DSL — plan_tickers>0 AND plan_matches_objective. A fix that
    embeds a malformed example would teach Opus the wrong grammar and still drift."""
    prompt = _build_prompt(bpg, _objective(bpg, "diversify"))
    example = _extract_example_plan(prompt)
    assert example is not None, "prompt must embed a concrete conforming example plan"
    tickers = bpg.plan_tickers(example)
    assert tickers, "the embedded example must reference real tickers (plan_tickers>0)"
    assert bpg.plan_matches_objective(example, _objective(bpg, "diversify")), (
        "the embedded diversify example must satisfy plan_matches_objective — "
        "otherwise the prompt teaches a non-conforming shape"
    )


def test_prompt_does_not_present_drift_tokens_as_the_vocabulary(bpg):
    """NEGATIVE guard: the prompt must not present the drift vocabulary as valid.
    The live failure was kind:'weighted' and a bare specified child kind:'node'.
    The prompt must not contain a `"kind": "weighted"` or `"kind": "node"` literal
    (which would teach the broken shape)."""
    prompt = _build_prompt(bpg, _objective(bpg, "diversify"))
    # Tolerant of whitespace around the colon; reject the drift literals.
    normalized = prompt.replace(" ", "").replace("'", '"')
    assert '"kind":"weighted"' not in normalized, "prompt teaches the drift kind 'weighted'"
    assert '"kind":"node"' not in normalized, "prompt teaches the drift child kind 'node'"


# ===========================================================================
# GROUP 2 — per-objective structural SIGNATURE in the prompt.
# ===========================================================================


def test_prompt_signature_is_objective_specific_not_a_constant_blob(bpg):
    """The four objectives must produce DIFFERENT prompts (each carries its own
    structural signature description) — not one constant blob with the name swapped.
    A constant prompt cannot steer Opus toward the per-objective AC-8 signature."""
    prompts = {name: _build_prompt(bpg, _objective(bpg, name)) for name in _OBJECTIVE_NAMES}
    # All four must be pairwise distinct.
    assert len(set(prompts.values())) == 4, "the four objective prompts must be distinct"


def test_cut_drawdown_prompt_describes_its_signature(bpg):
    """cut_drawdown -> regime gate (if) OR inverse-vol. The prompt must mention the
    inverse-vol or regime-gate construction so Opus emits a matching structure."""
    prompt = _build_prompt(bpg, _objective(bpg, "cut_drawdown"))
    assert "inverse_vol" in prompt or "if" in prompt, (
        "cut_drawdown prompt must describe a regime gate or inverse-vol structure"
    )


def test_lift_risk_adjusted_prompt_describes_momentum_quality_filter(bpg):
    """lift_risk_adjusted -> a momentum/quality FILTER. The prompt must mention a
    filter with a momentum-style sort so Opus emits the matching structure."""
    prompt = _build_prompt(bpg, _objective(bpg, "lift_risk_adjusted"))
    assert "filter" in prompt
    # At least one momentum/quality sort token must be named.
    momentum_tokens = ("cumulative-return", "moving-average-return", "momentum")
    assert any(tok in prompt for tok in momentum_tokens), (
        "lift_risk_adjusted prompt must name a momentum/quality filter sort"
    )


def test_volatility_mitigation_prompt_describes_low_vol_structure(bpg):
    """volatility_mitigation -> inverse-vol OR a low/min-vol filter. The prompt must
    describe a vol-reducing construction."""
    prompt = _build_prompt(bpg, _objective(bpg, "volatility_mitigation"))
    vol_tokens = (
        "inverse_vol",
        "standard-deviation",
        "low-vol",
        "low vol",
        "min-vol",
        "volatility",
    )
    assert any(tok in prompt for tok in vol_tokens), (
        "volatility_mitigation prompt must describe an inverse-vol / low-vol structure"
    )


# ===========================================================================
# GROUP 3 — the tool schema enum-constrains the vocabulary (defense-in-depth).
# ===========================================================================


def _collect_enum_values(schema) -> set:
    """Walk an arbitrary JSON-schema dict and collect every value listed in any
    'enum' or single-value 'const' anywhere in the tree."""
    found: set = set()
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("enum"), list):
                found.update(node["enum"])
            if "const" in node:
                found.add(node["const"])
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def test_tool_schema_constrains_kind_to_valid_enum(bpg):
    """_EMIT_BUILD_PLANS_TOOL must enum-constrain a NODE's `kind` to the valid set —
    the loose items:{type:object} schema is what let Opus emit kind:'weighted'.
    We walk the schema for the kind enum and assert the valid kinds are present and
    the drift token 'weighted' is NOT an allowed kind."""
    schema = bpg._EMIT_BUILD_PLANS_TOOL["input_schema"]
    enum_values = _collect_enum_values(schema)
    # The valid kind tokens must be enumerated somewhere in the schema.
    for kind in ("asset", "weight", "group", "filter"):
        assert kind in enum_values, f"tool schema does not enum-constrain kind {kind!r}"
    # The drift token must NOT be an allowed enum value.
    assert "weighted" not in enum_values, "tool schema must not allow kind 'weighted'"


def test_tool_schema_constrains_scheme_to_valid_enum(bpg):
    """The weight `scheme` must be enum-constrained to {equal, specified,
    inverse_vol} (market_cap is producer-deprecated, dropped by the compiler — it is
    not steered for). The schema must enumerate the valid schemes."""
    schema = bpg._EMIT_BUILD_PLANS_TOOL["input_schema"]
    enum_values = _collect_enum_values(schema)
    for scheme in ("equal", "specified", "inverse_vol"):
        assert scheme in enum_values, f"tool schema does not enum-constrain scheme {scheme!r}"


def test_tool_schema_is_not_the_loose_object_passthrough(bpg):
    """Regression guard: the items schema must NOT be the original loose
    {type:object} passthrough that permitted any shape. It must carry properties /
    enum constraints (depth > the original two-level shape)."""
    schema = bpg._EMIT_BUILD_PLANS_TOOL["input_schema"]
    enum_values = _collect_enum_values(schema)
    # A tightened schema enumerates real vocabulary; the loose original had none.
    assert enum_values, "tool schema carries no enum constraints — still the loose passthrough"


# ===========================================================================
# GROUP 4 — pipeline admits CONFORMING plans + rejects drift-vocabulary plans.
# ===========================================================================


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_conforming_mock_admits_at_least_one_plan_per_objective(bpg, monkeypatch, obj_name):
    """A realistic CONFORMING mocked Opus response (valid DSL, the right signature)
    -> generate_build_plans admits >=1 plan for every objective. Proves the pipeline
    accepts valid plans; guards the fix doesn't break admission."""
    obj = _objective(bpg, obj_name)
    universe = frozenset({"SPY", "QQQ", "TLT", "GLD", "IEF", "AAPL", "MSFT", "NVDA"})
    root = _conforming_root_for(obj_name)
    plans_in = [_plan(obj_name, root, plan_id=f"{obj_name}-1")]
    _patch_conforming_client(bpg, monkeypatch, plans_in)
    result = bpg.generate_build_plans(obj, universe, n_plans=12)
    assert result.plans, (
        f"a conforming {obj_name} plan must be admitted, got reason={result.reason!r}"
    )
    assert all(p.get("provenance") == bpg.PROVENANCE_BUILT_NEW for p in result.plans)


def test_validate_and_prune_rejects_unknown_container_kind(bpg):
    """ROBUSTNESS: a plan whose root uses an UNKNOWN container kind ('weighted', the
    real Opus drift) must be REJECTED by _validate_and_prune (returns None), NOT
    silently survive to the signature filter. Today the unknown kind passes through
    _prune_node's pass-through branch and survives with 0 tickers."""
    membership = frozenset({"VTI", "VEA", "BND", "TLT"})
    drift_plan = {
        "plan_id": "DIV-001",
        "objective": "diversify",
        "name": "X",
        "rebalance": "daily",
        "root": {
            "kind": "weighted",  # drift token — not a valid DSL kind
            "children": [
                {"kind": "node", "weight": 0.5, "node": {"kind": "asset", "ticker": "VTI"}},
                {"kind": "node", "weight": 0.5, "node": {"kind": "asset", "ticker": "BND"}},
            ],
        },
    }
    pruned = bpg._validate_and_prune(drift_plan, membership)
    assert pruned is None, (
        "a drift-vocabulary plan (unknown container kind) must be rejected at prune, "
        "not silently survive with 0 tickers"
    )


def test_validate_and_prune_rejects_zero_ticker_plan(bpg):
    """ROBUSTNESS: a plan that yields 0 extractable tickers after the walk is a
    degenerate plan and must be rejected (None), never admitted. (A plan with no
    tradeable tickers cannot become a valid Composer tree.)"""
    membership = frozenset({"SPY", "QQQ"})
    # A root whose only content is an unknown kind => 0 tickers.
    zero_plan = {
        "plan_id": "z",
        "objective": "diversify",
        "name": "Z",
        "rebalance": "daily",
        "root": {"kind": "weighted", "children": [{"kind": "node", "weight": 1.0}]},
    }
    pruned = bpg._validate_and_prune(zero_plan, membership)
    assert pruned is None


def test_full_drift_response_yields_zero_admitted_plans_with_reason(bpg, monkeypatch):
    """END-TO-END regression of the live failure: a mocked response carrying ONLY
    drift-vocabulary plans (kind:'weighted'/'node') yields zero admitted plans with
    an honest reason — never a crash, never a silently-malformed admitted plan."""
    universe = frozenset({"VTI", "VEA", "VWO", "BND", "TLT", "GLD", "VNQ"})
    drift_plans = [
        {
            "plan_id": "DIV-001",
            "objective": "diversify",
            "name": "Cross-Asset",
            "rebalance": "quarterly",
            "root": {
                "kind": "weighted",
                "children": [
                    {
                        "kind": "node",
                        "weight": 0.6,
                        "node": {
                            "kind": "weighted",
                            "children": [
                                {"kind": "asset", "ticker": "VTI", "weight": 0.5},
                                {"kind": "asset", "ticker": "BND", "weight": 0.5},
                            ],
                        },
                    },
                ],
            },
        }
    ]
    _patch_conforming_client(bpg, monkeypatch, drift_plans)
    result = bpg.generate_build_plans(_objective(bpg, "diversify"), universe, n_plans=12)
    assert result.plans == [], "drift-vocabulary plans must not be admitted"
    assert isinstance(result.reason, str) and result.reason


# ===========================================================================
# REVISE-1 — the if.condition SUB-GRAMMAR (live re-exam residual).
#
# Live re-exam: the prompt/schema fix taught the node vocabulary, and 8/12 plans
# compiled clean. But ALL 4 if/regime-gate plans dropped — the C2-fix never taught
# the nested if.condition sub-grammar. Real Opus emits
#   {"kind":"if","condition":"spy_above_200d_sma", ...}   (condition = a STRING label)
# but the C3 compiler expects (plan_tree_compiler.py:348-351)
#   condition = {lhs_fn, lhs_ticker, window, comparator, rhs:{fixed:num}|{fn,ticker,window}}
# so cond_dsl["lhs_fn"] on a str -> TypeError -> D-1 clean-drops the plan, killing
# cut_drawdown's defining regime-gate structure.
#
# The fix EXTENDS the same prompt-steer + schema-tighten to the condition sub-grammar
# (generator-side; the C3 compiler is correct for valid condition DSL, 38/38, untouched).
# The example must prove the CONDITION shape — i.e. it must COMPILE CLEAN through the
# real C3 compiler (tree not None + validate_tree==[]), not merely parse.
# ===========================================================================


@pytest.fixture(scope="module")
def compiler():
    """The C3 plan_tree_compiler — used to prove the embedded if example COMPILES
    clean (the gen<->compile contract crosses here). The compiler is NOT under test
    in this file (it is proven 38/38); we use it as the conformance oracle."""
    import advisors.plan_tree_compiler as _c  # noqa: PLC0415

    return _c


def _find_if_node(node):
    """Return the first kind=='if' or kind=='if_compound' NODE found in a DSL tree,
    or None. Iterative; handles weight/group/filter children, specified {node},
    and then/else branches."""
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if n.get("kind") in ("if", "if_compound"):
            return n
        children = n.get("children")
        if isinstance(children, list):
            for c in children:
                if isinstance(c, dict):
                    stack.append(c.get("node") if "node" in c else c)
        for branch in ("then", "else"):
            b = n.get(branch)
            if isinstance(b, list):
                stack.extend(b)
    return None


def _all_example_plans_in(prompt: str) -> list:
    """Return every build-plan-shaped JSON object embedded in the prompt (a plan has
    a 'root' dict carrying a 'kind')."""
    out = []
    for obj in _find_json_objects(prompt):
        if isinstance(obj, dict) and isinstance(obj.get("root"), dict) and "kind" in obj["root"]:
            out.append(obj)
    return out


def _gate_objective_names():
    """Objectives whose AC-8 signature is naturally satisfied by an if/regime gate.
    cut_drawdown's signature is explicitly 'regime gate (if/if_compound) OR inverse-vol'."""
    return ("cut_drawdown",)


# --- The prompt must TEACH the condition dict shape (not a string) ----------


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_prompt_teaches_if_condition_dict_field_names(bpg, obj_name):
    """The prompt must name the if.condition dict sub-fields so Opus emits a dict,
    not a string label. The drift was condition:'spy_above_200d_sma' (a string);
    the compiler needs {lhs_fn, lhs_ticker, comparator, rhs}."""
    prompt = _build_prompt(bpg, _objective(bpg, obj_name))
    for field in ("lhs_fn", "lhs_ticker", "comparator", "rhs"):
        assert field in prompt, (
            f"prompt for {obj_name} must teach the if.condition field {field!r} "
            f"(so Opus emits a condition DICT, not a string label)"
        )


def test_prompt_teaches_condition_rhs_fixed_and_ticker_forms(bpg):
    """The condition rhs is a union {fixed: num} | {fn, ticker, window}. The prompt
    must teach at least the 'fixed' fixed-value form (the simplest regime gate)."""
    prompt = _build_prompt(bpg, _objective(bpg, "cut_drawdown"))
    assert "fixed" in prompt, "prompt must teach the condition rhs {fixed: num} form"


def test_prompt_warns_against_string_condition(bpg):
    """NEGATIVE guard: the prompt must steer AWAY from a bare string condition — the
    exact drift. It should not present a string-label condition as valid. We assert
    the prompt does not contain the drift literal label and DOES contain the dict
    shape signal (a 'condition' object with lhs_fn)."""
    prompt = _build_prompt(bpg, _objective(bpg, "cut_drawdown"))
    normalized = prompt.replace(" ", "").replace("'", '"')
    # The known drift label must not be taught as a valid condition value.
    assert '"condition":"spy_above_200d_sma"' not in normalized
    # A string-valued condition example must not appear (condition mapped to a
    # bare quoted scalar). The dict form pairs 'condition' with '{'.
    assert "lhs_fn" in prompt, "the prompt must show the condition as a dict with lhs_fn"


# --- The embedded if example must COMPILE CLEAN (proves the condition shape) -


def test_prompt_embeds_an_if_example_that_compiles_clean(bpg, compiler):
    """ADVERSARIAL CORE of the Revise: at least one embedded example must contain an
    if/if_compound node whose CONDITION is a proper dict, AND that example must
    COMPILE CLEAN through the real C3 compiler (tree not None + validate_tree==[]).
    A prompt that teaches the condition grammar but embeds a non-compiling example
    would still drift. The example must PROVE the condition shape end-to-end."""
    # Search every embedded example across the gate-capable objectives for one
    # carrying an if node that compiles clean.
    found_compiling_if = False
    for obj_name in _gate_objective_names():
        prompt = _build_prompt(bpg, _objective(bpg, obj_name))
        for example in _all_example_plans_in(prompt):
            if _find_if_node(example["root"]) is None:
                continue
            result = compiler.compile_plan(example)
            if result.tree is not None and symphony_schema.validate_tree(result.tree) == []:
                found_compiling_if = True
                break
        if found_compiling_if:
            break
    assert found_compiling_if, (
        "no embedded if/regime-gate example compiles clean through the C3 compiler; "
        "the prompt must embed a conforming if example whose condition is a dict that "
        "compile_plan accepts (tree not None + validate_tree==[])"
    )


def test_embedded_if_example_condition_is_a_dict_not_a_string(bpg):
    """The embedded if example's condition must be a DICT (the compiler contract),
    never a string label (the drift). Direct structural assertion on the example."""
    prompt = _build_prompt(bpg, _objective(bpg, "cut_drawdown"))
    if_example = None
    for example in _all_example_plans_in(prompt):
        node = _find_if_node(example["root"])
        if node is not None:
            if_example = node
            break
    assert if_example is not None, "cut_drawdown prompt must embed an if-node example"
    cond = if_example.get("condition")
    assert isinstance(cond, dict), (
        f"the embedded if example's condition must be a dict, got {type(cond).__name__} "
        f"({cond!r}) — a string condition is the live-exam drift"
    )


# --- The tool schema must constrain if.condition to an OBJECT ----------------


def test_tool_schema_constrains_if_condition_to_object_not_string(bpg):
    """_EMIT_BUILD_PLANS_TOOL must constrain a condition to an OBJECT so Opus cannot
    structurally emit a bare string condition. We walk the schema for a 'condition'
    property and assert its declared type is 'object' (or it carries object
    sub-properties), and that it is NOT typed as a plain string."""
    schema = bpg._EMIT_BUILD_PLANS_TOOL["input_schema"]
    condition_schemas = _find_property_schemas(schema, "condition")
    assert condition_schemas, (
        "tool schema declares no 'condition' property constraint — Opus can emit any "
        "condition shape (including the bare string that caused the live drift)"
    )
    for cs in condition_schemas:
        declared_type = cs.get("type")
        # The condition must be object-typed (or describe object sub-fields),
        # never a bare string.
        is_object = declared_type == "object" or "properties" in cs
        assert is_object, (
            f"a 'condition' schema entry is typed {declared_type!r}, not an object — "
            f"it must constrain the condition to a dict, not allow a string label"
        )
        assert declared_type != "string", "condition must not be schema-typed as a string"


def _find_property_schemas(schema, prop_name: str) -> list:
    """Return every sub-schema declared under properties[prop_name] anywhere in the
    schema tree (walks nested objects/arrays/items)."""
    out = []
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            props = node.get("properties")
            if (
                isinstance(props, dict)
                and prop_name in props
                and isinstance(props[prop_name], dict)
            ):
                out.append(props[prop_name])
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


# --- A conforming-mock if-based plan is admitted AND compiles clean ----------


def test_conforming_if_plan_admitted_and_compiles_clean(bpg, compiler, monkeypatch):
    """A CONFORMING mocked if-based cut_drawdown plan (condition = a proper dict) is
    admitted by generate_build_plans AND its admitted form compiles clean through the
    C3 compiler. This is the end-to-end regime-gate contract the live exam checks:
    a regime-gated symphony must reach a validate_tree-clean Composer tree."""
    universe = frozenset({"SPY", "UVXY", "TLT", "IEF", "GLD"})
    if_root = {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": "SPY",
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": 80},
        },
        "then": [_equal([_asset("UVXY"), _asset("TLT")])],
        "else": [_inverse_vol([_asset("SPY"), _asset("IEF"), _asset("GLD")])],
    }
    plan_in = _plan("cut_drawdown", if_root, plan_id="cd-if-1")
    _patch_conforming_client(bpg, monkeypatch, [plan_in])
    result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), universe, n_plans=12)
    assert result.plans, (
        f"a conforming if-based cut_drawdown plan must be admitted, reason={result.reason!r}"
    )
    admitted = result.plans[0]
    compiled = compiler.compile_plan(admitted)
    assert compiled.tree is not None, (
        f"the admitted regime-gate plan must compile clean, got reason={compiled.reason!r}"
    )
    assert symphony_schema.validate_tree(compiled.tree) == []


def test_string_condition_plan_does_not_silently_admit_as_compilable(bpg, compiler, monkeypatch):
    """SECONDARY guard (steering is the primary fix): a plan whose if.condition is a
    STRING (the drift) must NOT yield a clean-compiling admitted plan. Either it is
    rejected at admission, or if admitted it fails to compile (tree None) — it must
    never silently surface as a usable regime-gate symphony."""
    universe = frozenset({"SPY", "UVXY", "TLT", "IEF"})
    drift_root = {
        "kind": "if",
        "condition": "spy_above_200d_sma",  # drift: a string label, not a dict
        "then": [_equal([_asset("UVXY")])],
        "else": [_inverse_vol([_asset("SPY"), _asset("IEF")])],
    }
    plan_in = _plan("cut_drawdown", drift_root, plan_id="cd-drift-1")
    _patch_conforming_client(bpg, monkeypatch, [plan_in])
    result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), universe, n_plans=12)
    # If admitted at all, it must NOT compile clean (the string condition is invalid).
    for admitted in result.plans:
        node = _find_if_node(admitted.get("root", {}))
        if node is not None and not isinstance(node.get("condition"), dict):
            compiled = compiler.compile_plan(admitted)
            assert compiled.tree is None, (
                "a string-condition regime-gate plan must not compile to a usable tree"
            )


# ===========================================================================
# REVISE-2 — the if_compound COMPOUND-CONDITION union (FINAL grammar gap).
#
# Operator v1 directive: "compound conditions ALL in v1, no fast-follows." if_compound
# IS compound conditions. The flat-if Revise (Revise-1) taught only the flat if.condition
# dict + embedded a flat-if worked example + schema-constrained the flat condition. But
# if_compound (compound / multi-condition regime gates) was taught in prompt TEXT only —
# no worked compiling example, and its compound-condition UNION
#   {type:"binary"|"binary_compound"|"compound", operator:"any"|"all", conditions:[...], tickers:[...]}
# is NOT in the schema's condition constraint. That is the same under-exemplified-construct
# pattern that dropped node-vocab and then if.condition — now one level deeper, and the LAST
# grammar gap (the C3 compiler already handles the recursive condition union, 38/38 goldens).
#
# This Revise extends the SAME prompt-steer + schema-tighten to the compound union. The
# embedded if_compound example must COMPILE CLEAN through the real C3 compiler (the
# boundary-crossing oracle), proving the compound-condition shape end-to-end.
# (Verified pre-RED: if_compound with binary / binary_compound / compound conditions all
# compile validate_tree-clean through compile_plan.)
# ===========================================================================


def _binary_compound_cond(fn, tickers, comparator, const, window, operator="any"):
    return {
        "type": "binary_compound",
        "fn": fn,
        "tickers": list(tickers),
        "comparator": comparator,
        "rhs": {"const": const},
        "window": window,
        "operator": operator,
    }


def _compound_cond(operator, conditions):
    return {"type": "compound", "operator": operator, "conditions": conditions}


def _if_compound(condition, then, els):
    return {"kind": "if_compound", "condition": condition, "then": then, "else": els}


def _find_if_compound_node(node):
    """Return the first kind=='if_compound' NODE in a DSL tree, or None."""
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if n.get("kind") == "if_compound":
            return n
        children = n.get("children")
        if isinstance(children, list):
            for c in children:
                if isinstance(c, dict):
                    stack.append(c.get("node") if "node" in c else c)
        for branch in ("then", "else"):
            b = n.get(branch)
            if isinstance(b, list):
                stack.extend(b)
    return None


# --- The prompt must TEACH the compound-condition union ----------------------


@pytest.mark.parametrize("obj_name", _OBJECTIVE_NAMES)
def test_prompt_teaches_if_compound_condition_union(bpg, obj_name):
    """The prompt must teach the if_compound compound-condition union so Opus can
    emit a multi-condition regime gate. It must name the condition 'type' tag and
    the compound sub-grammar tokens (operator any/all, conditions, tickers)."""
    prompt = _build_prompt(bpg, _objective(bpg, obj_name))
    # The union is selected by a 'type' tag with these values.
    assert "binary_compound" in prompt or "compound" in prompt, (
        f"prompt for {obj_name} must teach the compound-condition union (type values)"
    )
    # The any/all operator and the conditions/tickers list fields must be named.
    assert "operator" in prompt, "prompt must name the compound 'operator' (any/all) field"
    assert "conditions" in prompt or "tickers" in prompt, (
        "prompt must name the compound 'conditions'[] / 'tickers'[] fields"
    )


def test_prompt_names_if_compound_kind_and_condition_type_tag(bpg):
    """The prompt must present kind='if_compound' and that its condition is a TYPED
    union (a 'type' field), distinct from the flat-if condition shape."""
    prompt = _build_prompt(bpg, _objective(bpg, "cut_drawdown"))
    assert "if_compound" in prompt, "prompt must name the if_compound node kind"
    assert "type" in prompt, "prompt must teach the condition 'type' discriminator"


# --- The embedded if_compound example must COMPILE CLEAN ---------------------


def test_prompt_embeds_an_if_compound_example_that_compiles_clean(bpg, compiler):
    """ADVERSARIAL CORE of Revise-2: at least one embedded example must contain an
    if_compound node whose condition is a proper typed union, AND that example must
    COMPILE CLEAN through the real C3 compiler (tree not None + validate_tree==[]).
    A prompt that merely describes the compound union in prose without a compiling
    worked example is exactly what let if.condition drift — this closes it."""
    found = False
    for obj_name in _gate_objective_names():
        prompt = _build_prompt(bpg, _objective(bpg, obj_name))
        for example in _all_example_plans_in(prompt):
            if _find_if_compound_node(example["root"]) is None:
                continue
            result = compiler.compile_plan(example)
            if result.tree is not None and symphony_schema.validate_tree(result.tree) == []:
                found = True
                break
        if found:
            break
    assert found, (
        "no embedded if_compound example compiles clean through the C3 compiler; the "
        "prompt must embed a worked compound-condition example whose condition is a typed "
        "union that compile_plan accepts (tree not None + validate_tree==[])"
    )


def test_embedded_if_compound_example_condition_is_a_typed_union(bpg):
    """The embedded if_compound example's condition must be a DICT carrying a 'type'
    discriminator (binary / binary_compound / compound) — never a flat if-shape or a
    string. Direct structural assertion."""
    prompt = _build_prompt(bpg, _objective(bpg, "cut_drawdown"))
    node = None
    for example in _all_example_plans_in(prompt):
        n = _find_if_compound_node(example["root"])
        if n is not None:
            node = n
            break
    assert node is not None, "cut_drawdown prompt must embed an if_compound example"
    cond = node.get("condition")
    assert isinstance(cond, dict), (
        f"if_compound condition must be a dict, got {type(cond).__name__}"
    )
    assert cond.get("type") in ("binary", "binary_compound", "compound"), (
        f"if_compound condition must carry a valid 'type' discriminator, got {cond.get('type')!r}"
    )


# --- The tool schema must accept the compound union for if_compound.condition -


def test_tool_schema_condition_accepts_compound_union_fields(bpg):
    """The tool schema's condition constraint must accommodate the compound union —
    not ONLY the flat-if shape. Opus must be able to emit a typed compound condition
    (type/operator/conditions/tickers) within the schema. We assert the schema's
    condition constraint references the union discriminator/fields (so a compound
    condition is schema-valid), not solely the flat lhs_fn shape."""
    schema = bpg._EMIT_BUILD_PLANS_TOOL["input_schema"]
    enum_values = _collect_enum_values(schema)
    # Walk all condition-property schemas + the whole schema text for union tokens.
    cond_schemas = _find_property_schemas(schema, "condition")
    # The union is expressible if the schema enumerates the type discriminator values
    # OR declares condition-union fields (operator/conditions/tickers/type) somewhere.
    union_type_present = {"binary", "binary_compound", "compound"} & set(enum_values)
    union_fields_present = bool(
        _find_property_schemas(schema, "conditions")
        or _find_property_schemas(schema, "operator")
        or _find_property_schemas(schema, "type")
    )
    assert union_type_present or union_fields_present, (
        "tool schema does not express the if_compound compound-condition union "
        "(no type-discriminator enum and no operator/conditions/tickers fields) — "
        "Opus cannot structurally emit a valid compound condition"
    )
    # And the condition is still object-typed (never a bare string), preserving Revise-1.
    for cs in cond_schemas:
        assert cs.get("type") != "string", "condition must not be schema-typed as a string"


# --- A conforming-mock if_compound plan is admitted AND compiles clean -------


def test_conforming_if_compound_plan_admitted_and_compiles_clean(bpg, compiler, monkeypatch):
    """A CONFORMING mocked if_compound cut_drawdown plan (a typed compound condition)
    is admitted AND its admitted form compiles clean through the C3 compiler — the
    multi-condition regime-gate end-to-end contract the operator's v1 directive
    requires. (compound = all-of-N broadcast conditions.)"""
    universe = frozenset({"SPY", "QQQ", "UVXY", "TLT", "IEF"})
    compound_root = _if_compound(
        _compound_cond(
            "all",
            [
                _binary_compound_cond("relative-strength-index", ["SPY"], "gt", 70, 14),
                _binary_compound_cond("max-drawdown", ["QQQ"], "lt", 20, 30),
            ],
        ),
        then=[_equal([_asset("UVXY")])],
        els=[_inverse_vol([_asset("SPY"), _asset("IEF")])],
    )
    plan_in = _plan("cut_drawdown", compound_root, plan_id="cd-compound-1")
    _patch_conforming_client(bpg, monkeypatch, [plan_in])
    result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), universe, n_plans=12)
    assert result.plans, (
        f"a conforming if_compound cut_drawdown plan must be admitted, reason={result.reason!r}"
    )
    admitted = result.plans[0]
    compiled = compiler.compile_plan(admitted)
    assert compiled.tree is not None, (
        f"the admitted compound regime-gate plan must compile clean, reason={compiled.reason!r}"
    )
    assert symphony_schema.validate_tree(compiled.tree) == []


# ===========================================================================
# BINARY-ENCODING FIX — the compound's BINARY LEAF must use the canonical FLAT
# shape (the live compound-gate probe dropped 2/4 with a KeyError here).
#
# The live failure: Opus, when it reaches for a plain `type:"binary"` sub-condition
# inside a compound, emits it in the FLAT shape (lhs_fn/lhs_ticker/window + rhs:{fixed})
# it learned from the live-proven flat-if example — but the compiler's compound
# binary leaf read the NESTED `lhs:{...}`/{const} shape → cond["lhs"] KeyError. The
# fix unifies onto the FLAT encoding everywhere. The generator must TEACH the flat
# binary leaf inside a compound (a worked type:"binary" sub-condition), not only
# binary_compound — otherwise Opus has no flat-binary-leaf example and falls back to
# the if shape.
# ===========================================================================


def _binary_leaf_flat(lhs_fn, lhs_ticker, window, comparator, fixed):
    """A FLAT binary leaf sub-condition (canonical encoding): flat lhs_fn/lhs_ticker/
    window + rhs:{fixed} — the same field names as the flat-if condition."""
    return {
        "type": "binary",
        "lhs_fn": lhs_fn,
        "lhs_ticker": lhs_ticker,
        "window": window,
        "comparator": comparator,
        "rhs": {"fixed": fixed},
    }


def test_prompt_teaches_flat_binary_leaf_sub_condition(bpg):
    """The prompt must teach a type:"binary" sub-condition in the FLAT shape (lhs_fn
    + rhs:{fixed}), so Opus emits a binary leaf inside a compound in the canonical
    encoding rather than guessing. The prompt must present a 'binary' condition type
    that uses the flat lhs_fn field (not a nested lhs:{...})."""
    prompt = _build_prompt(bpg, _objective(bpg, "cut_drawdown"))
    # The binary leaf type must be named as part of the union.
    assert "binary" in prompt
    # The flat field names the canonical binary leaf uses must be taught.
    assert "lhs_fn" in prompt
    # The prompt must NOT teach the legacy nested binary leaf shape lhs:{fn,ticker,...}
    # as the canonical form (that's the discarded encoding).
    normalized = prompt.replace(" ", "").replace("'", '"')
    assert '"lhs":{"fn"' not in normalized, (
        "prompt must not teach the legacy nested binary-leaf encoding lhs:{fn,...}"
    )


def test_embedded_compound_example_uses_flat_binary_leaf_that_compiles(bpg, compiler):
    """At least one embedded compound example must contain a type:"binary" leaf in
    the FLAT shape that COMPILES CLEAN through the C3 compiler. binary_compound alone
    is not enough — the live drop was on a plain binary leaf. (If the example uses
    only binary_compound leaves, Opus still has no flat-binary-leaf model.)"""
    found_flat_binary = False
    for obj_name in _gate_objective_names():
        prompt = _build_prompt(bpg, _objective(bpg, obj_name))
        for example in _all_example_plans_in(prompt):
            # Look for a compound whose sub-conditions include a type:"binary" leaf.
            if not _has_flat_binary_leaf(example):
                continue
            result = compiler.compile_plan(example)
            if result.tree is not None and symphony_schema.validate_tree(result.tree) == []:
                found_flat_binary = True
                break
        if found_flat_binary:
            break
    assert found_flat_binary, (
        "no embedded example contains a flat type:'binary' leaf inside a compound that "
        "compiles clean; Opus needs a worked flat-binary-leaf example or it falls back "
        "to the if-shape and the compound KeyErrors"
    )


def _has_flat_binary_leaf(plan):
    """True if the plan has a compound condition with a type:'binary' sub-condition
    carrying the flat lhs_fn field."""
    stack = [plan]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("type") == "compound":
                for sub in n.get("conditions", []) or []:
                    if isinstance(sub, dict) and sub.get("type") == "binary" and "lhs_fn" in sub:
                        return True
            for v in n.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(n, list):
            stack.extend(n)
    return False


def test_conforming_compound_with_flat_binary_leaf_admitted_and_compiles(
    bpg, compiler, monkeypatch
):
    """A CONFORMING mocked compound plan whose leaf is a FLAT binary (the exact live
    drop shape) is admitted AND compiles clean — the regression of the 2/4 KeyError.
    This is the end-to-end generator→compiler contract for a flat binary leaf."""
    universe = frozenset({"SPY", "QQQ", "UVXY", "TLT", "IEF"})
    compound_root = _if_compound(
        _compound_cond(
            "all",
            [
                _binary_leaf_flat("relative-strength-index", "SPY", 14, "gt", 70),
                _binary_leaf_flat("max-drawdown", "QQQ", 30, "lt", 20),
            ],
        ),
        then=[_equal([_asset("UVXY")])],
        els=[_inverse_vol([_asset("SPY"), _asset("IEF")])],
    )
    plan_in = _plan("cut_drawdown", compound_root, plan_id="cd-flatbin-1")
    _patch_conforming_client(bpg, monkeypatch, [plan_in])
    result = bpg.generate_build_plans(_objective(bpg, "cut_drawdown"), universe, n_plans=12)
    assert result.plans, (
        f"a conforming flat-binary-leaf compound must be admitted, reason={result.reason!r}"
    )
    admitted = result.plans[0]
    compiled = compiler.compile_plan(admitted)
    assert compiled.tree is not None, (
        f"the admitted flat-binary-leaf compound must compile clean, reason={compiled.reason!r}"
    )
    assert symphony_schema.validate_tree(compiled.tree) == []
