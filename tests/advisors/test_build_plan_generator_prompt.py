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
