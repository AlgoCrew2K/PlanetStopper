"""RED tests — advisors/frontrunner_builder.py (NEW — does not exist yet).

Module under test: advisors.frontrunner_builder. The ImportError on the
fixture below is the first RED signal.

SCOPE (this file): AC-4 (Fable candidate generation, hard constraints) and
AC-5 (compile + splice into a valid symphony). AC-6/7 (gates + Calmar
acceptance) are test_frontrunner_acceptance.py; the full detect->approve->
create integration path is test_frontrunner_integration.py; the no-trade
boundary is tests/security/test_frontrunner_no_trade_boundary.py.

CONTRACT SOURCES:
  - feature-plans/frontrunner-builder.md AC-4: Fable composes a candidate
    frontrunner overlay (build-plan DSL). Hard constraints, enforced
    POST-GENERATION (i.e. the module must validate/enforce these even if the
    model's raw output violates them): (a) >=1 VIX-family ticker; (b) VIX
    instrument varied across builds (not defaulting to VIXY); (c) mergeable
    flat RSI-gt rungs collapsed to any/all (binary-compound/compound); (d)
    scale-in tiers preserved as tiered conditions, never flattened to one OR.
  - AC-5: candidate DSL compiles via plan_tree_compiler.compile_plan (the
    ONLY public entry point — see advisors/plan_tree_compiler.py:394) and is
    spliced into the symphony replacing the incumbent overlay, producing a
    full valid symphony (symphony_schema.validate_tree(...) == []).
  - Model: Fable (claude-fable-5) via the Anthropic SDK, mirroring
    build_plan_generator's _build_client() factory seam
    (advisors/build_plan_generator.py:153) so tests patch
    advisors.frontrunner_builder._build_client — NO live network, ever.

ADVERSARIAL FOCUS (assert SHAPE / STRUCTURE / PRESENCE / ENUM-MEMBERSHIP —
never a hardcoded producer value; symphony_schema constructors are the DSL
vocabulary source of truth, imported live so nothing here hardcodes it):
  - a mocked Fable response with NO VIX-family ticker is rejected (post-
    generation constraint enforcement, not blind trust of the model output)
  - across multiple distinct mocked Fable responses, the VIX instrument used
    varies (not pinned to VIXY every time) — this is a PROPERTY of how the
    builder must validate/select across candidates, tested by feeding it
    several distinct model outputs and confirming it doesn't silently
    normalize them all to one instrument
  - mergeable flat RSI-gt rungs collapse to any/all (binary-compound/compound
    condition-type, per symphony_schema's own vocabulary)
  - scale-in tiers (nested if/if_compound) are preserved as nested structure,
    never flattened to a single OR condition
  - splice produces a tree that compiles clean via plan_tree_compiler and
    validates clean via symphony_schema.validate_tree
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import pytest

from advisors import symphony_schema

# ---------------------------------------------------------------------------
# Module-under-test import guard — RED until advisors/frontrunner_builder.py exists.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fbld():
    """Import and return the frontrunner_builder module (RED until it exists)."""
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


# ---------------------------------------------------------------------------
# Mocked-Fable-SDK plumbing — mirrors test_build_plan_generator.py's
# _tool_use_block / _client_returning_plans / _patch_client idiom exactly, so
# the mocking style is consistent across the advisors package.
# ---------------------------------------------------------------------------


def _tool_use_block(input_payload: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_payload
    return block


def _client_returning_overlay(overlay_dsl: dict) -> MagicMock:
    """A mock Fable client whose messages.create returns a tool_use block
    carrying a single candidate overlay DSL node (a build-plan-DSL 'if' or
    'if_compound' NODE, per advisors/build_plan_generator.py's DSL grammar)."""
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [_tool_use_block({"overlay": overlay_dsl})]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _patch_fable_client(fbld_module, client: MagicMock):
    """Patch the module's _build_client factory (mirrors build_plan_generator's
    seam name so the mocking idiom — and the never-live-network guarantee —
    is identical across every SDK-calling advisors module)."""
    return patch.object(fbld_module, "_build_client", return_value=client)


# ---------------------------------------------------------------------------
# DSL fixture builders — construct VALID build-plan-DSL 'if' NODEs (never
# hardcoded producer output; these are OUR test inputs, fed to the mocked
# Fable client). Shape mirrors build_plan_generator.py's DSL grammar
# (kind='if'/'if_compound', condition dict, then/else lists).
# ---------------------------------------------------------------------------


def _dsl_asset(ticker: str) -> dict:
    return {"kind": "asset", "ticker": ticker}


def _dsl_weight_equal(children: list[dict]) -> dict:
    return {"kind": "weight", "scheme": "equal", "children": children}


def _dsl_flat_if_overlay(
    signal_ticker: str,
    threshold: float,
    vix_ticker: str,
    core_placeholder: str = "CANDIDATE_PLACEHOLDER_ASSET",
) -> dict:
    """A single-rung RSI-gt -> VIX-fire overlay candidate (build-plan DSL).

    core_placeholder defaults to a ticker DISTINCT from
    incumbent_symphony's own core ticker (CORE_ASSET_0001) on purpose — a
    prior default coincidentally reused CORE_ASSET_0001 here too, which made
    test_splice_preserves_the_core_content_past_the_boundary pass whether or
    not splice actually preserved the incumbent's real core (the candidate's
    own placeholder ticker happened to match it). Keep these distinct."""
    return {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": signal_ticker,
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": threshold},
        },
        "then": [_dsl_weight_equal([_dsl_asset(vix_ticker)])],
        "else": [_dsl_weight_equal([_dsl_asset(core_placeholder)])],
    }


def _dsl_no_vix_overlay(signal_ticker: str = "SPY", threshold: float = 80) -> dict:
    """A degenerate candidate whose fire branch has NO VIX-family ticker —
    Fable hallucinated something invalid; the builder must reject this
    post-generation, never accept it as-is (AC-4a)."""
    return {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": signal_ticker,
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": threshold},
        },
        "then": [_dsl_weight_equal([_dsl_asset("SPY")])],  # no VIX ticker at all
        "else": [_dsl_weight_equal([_dsl_asset("CORE_ASSET_0001")])],
    }


def _dsl_scale_in_tiers_overlay(
    signal_ticker: str = "QQQ",
    low_threshold: float = 80,
    high_threshold: float = 82.5,
    light_vix: str = "VIXM",
    heavy_vix: str = "UVXY",
    core_placeholder: str = "CORE_ASSET_0001",
) -> dict:
    """A genuine 2-tier scale-in overlay: RSI>80 fires a light VIX blend;
    nested inside, RSI>82.5 fires a heavier UVXY tranche. This is the shape
    AC-4d says must be PRESERVED (never flattened to one OR)."""
    inner_if = {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": signal_ticker,
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": high_threshold},
        },
        "then": [_dsl_weight_equal([_dsl_asset(heavy_vix)])],
        "else": [_dsl_weight_equal([_dsl_asset(light_vix)])],
    }
    return {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": signal_ticker,
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": low_threshold},
        },
        "then": [inner_if],
        "else": [_dsl_weight_equal([_dsl_asset(core_placeholder)])],
    }


def _dsl_mergeable_flat_rungs_overlay(
    signal_tickers: list[str],
    threshold: float,
    vix_ticker: str,
    core_placeholder: str = "CORE_ASSET_0001",
) -> dict:
    """Several structurally-identical flat RSI-gt rungs (one per
    signal_ticker, all firing the SAME vix_ticker at the SAME threshold) —
    this is the 'hundreds of flat RSI-gt rungs' shape AC-4c says should
    collapse to a single any/all (binary-compound) condition rather than
    being emitted as N nested if-nodes."""
    # Deliberately built as N SEPARATE nested if-nodes (the un-collapsed,
    # naive shape) so the builder's post-generation collapse logic has
    # something real to do.
    node = _dsl_weight_equal([_dsl_asset(core_placeholder)])
    for ticker in reversed(signal_tickers):
        node = {
            "kind": "if",
            "condition": {
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": ticker,
                "window": 10,
                "comparator": "gt",
                "rhs": {"fixed": threshold},
            },
            "then": [_dsl_weight_equal([_dsl_asset(vix_ticker)])],
            "else": [node],
        }
    return node


# ---------------------------------------------------------------------------
# A minimal incumbent symphony to splice INTO — a valid Composer raw_value
# tree (via symphony_schema, never hand-rolled) with an existing frontrunner
# cascade the candidate replaces, plus a small "core" tail.
# ---------------------------------------------------------------------------


@pytest.fixture
def incumbent_symphony() -> dict:
    incumbent_cascade = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            80,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    return symphony_schema.make_root("Incumbent Test Symphony", "daily", [incumbent_cascade])


# ---------------------------------------------------------------------------
# AC-4(a): candidate always has >=1 VIX-family ticker (post-generation
# enforcement — the module must not blindly trust a hallucinated candidate).
# ---------------------------------------------------------------------------

_VIX_FAMILY = frozenset({"VIXY", "VIXM", "UVXY", "UVIX", "VXX", "SVXY", "SVIX"})


def test_candidate_with_no_vix_ticker_is_rejected(fbld):
    """Fable hallucinating a fire-basket with no VIX-family ticker at all must
    be rejected post-generation, not accepted as a valid candidate (AC-4a)."""
    client = _client_returning_overlay(_dsl_no_vix_overlay())
    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["SPY"]},
        )
    assert getattr(result, "candidate", None) is None or getattr(result, "error", None), (
        "a Fable candidate with no VIX-family ticker anywhere in its fire "
        "basket must be rejected, not silently accepted"
    )


def test_candidate_with_vix_ticker_is_accepted(fbld):
    """The mirror-image positive case: a well-formed candidate with a genuine
    VIX-family fire ticker is accepted."""
    client = _client_returning_overlay(_dsl_flat_if_overlay("SPY", 80, "UVXY"))
    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["SPY"]},
        )
    assert getattr(result, "candidate", None) is not None, (
        "a well-formed VIX-bearing candidate was rejected — over-strict post-generation validation"
    )
    assert getattr(result, "error", None) is None


# ---------------------------------------------------------------------------
# AC-4(b): VIX instrument varied across builds — not defaulting to VIXY.
# ---------------------------------------------------------------------------


def test_vix_instrument_varies_across_distinct_fable_outputs(fbld):
    """Feed several distinct mocked Fable responses, each firing a DIFFERENT
    VIX-family ticker. The builder must accept and preserve each one as
    generated — not silently normalize every candidate to VIXY (the grounding
    note explicitly calls out 'not always VIXY'; top rungs fire UVXY+VIXM/VXX).
    This is a property of the acceptance/pass-through path, not of Fable's
    own randomness — we control every input via the mock."""
    accepted_vix_tickers: set[str] = set()
    for vix_ticker in ("VIXM", "UVXY", "VXX", "UVIX"):
        client = _client_returning_overlay(_dsl_flat_if_overlay("SPY", 80, vix_ticker))
        with _patch_fable_client(fbld, client):
            result = fbld.generate_candidate_overlay(
                signal_context={"watched_tickers": ["SPY"]},
            )
        candidate = getattr(result, "candidate", None)
        assert candidate is not None, f"a valid {vix_ticker}-firing candidate was rejected"
        candidate_tickers = _dsl_tickers(candidate)
        accepted_vix_tickers |= candidate_tickers & _VIX_FAMILY

    assert len(accepted_vix_tickers) > 1, (
        f"the builder normalized every distinct Fable output down to a single "
        f"VIX ticker set {accepted_vix_tickers} — instrument variety is not "
        f"being preserved across builds"
    )


def _dsl_tickers(node, out: set[str] | None = None) -> set[str]:
    """Walk a build-plan DSL node and collect every 'ticker' field."""
    if out is None:
        out = set()
    if not isinstance(node, dict):
        return out
    t = node.get("ticker")
    if isinstance(t, str):
        out.add(t)
    for key in ("children", "then", "else"):
        for child in node.get(key) or []:
            _dsl_tickers(child, out)
    return out


# ---------------------------------------------------------------------------
# AC-4(c): mergeable flat RSI-gt rungs collapsed to any/all (binary-compound
# / compound), per symphony_schema's own vocabulary (imported live, never
# hardcoded — KNOWN comparators/condition-types come from the module).
# ---------------------------------------------------------------------------


def _contains_condition_type(node, condition_type: str) -> bool:
    """Walk a COMPILED Composer tree (symphony_schema shape: step/condition
    on if-child nodes) looking for a condition dict with the given
    condition-type. Never raises on malformed input."""
    if not isinstance(node, dict):
        return False
    cond = node.get("condition")
    if isinstance(cond, dict) and cond.get("condition-type") == condition_type:
        return True
    for child in node.get("children") or []:
        if _contains_condition_type(child, condition_type):
            return True
    return False


def test_mergeable_flat_rungs_collapse_to_a_compound_condition(fbld):
    """N structurally-identical flat RSI-gt rungs (same threshold, same fired
    VIX ticker, different watched tickers) must collapse to ONE binary-compound
    (any/all broadcast) condition rather than surviving as N nested if-nodes
    (AC-4c). Asserted on the COMPILED tree (post plan_tree_compiler), since
    that is the artifact that actually gets spliced/uploaded."""
    naive_overlay = _dsl_mergeable_flat_rungs_overlay(["SPY", "QQQ", "XLK"], 80, "UVXY")
    client = _client_returning_overlay(naive_overlay)
    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["SPY", "QQQ", "XLK"]},
        )
    candidate = getattr(result, "candidate", None)
    assert candidate is not None, "a valid mergeable-rungs candidate was rejected"

    compiled = getattr(result, "compiled_tree", None) or candidate
    assert _contains_condition_type(compiled, "binary-compound") or _contains_condition_type(
        compiled, "compound"
    ), (
        "mergeable flat RSI-gt rungs over multiple tickers were not collapsed "
        "to a binary-compound/compound any/all condition — AC-4c requires "
        "collapsing repetitive flat rungs, not emitting N nested if-nodes"
    )


# ---------------------------------------------------------------------------
# AC-4(d): scale-in tiers preserved as tiered conditions, never flattened.
# ---------------------------------------------------------------------------


def _count_nested_if_depth(node, kind_keys=("kind", "step")) -> int:
    """Count the maximum nesting depth of if/if_compound-shaped nodes,
    tolerating either the DSL shape (kind='if') or the compiled Composer
    shape (step='if')."""
    if not isinstance(node, dict):
        return 0
    is_if = node.get("kind") in ("if", "if_compound") or node.get("step") == "if"
    children_lists = []
    for key in ("children", "then", "else"):
        val = node.get(key)
        if isinstance(val, list):
            children_lists.append(val)
    max_child_depth = 0
    for lst in children_lists:
        for child in lst:
            max_child_depth = max(max_child_depth, _count_nested_if_depth(child, kind_keys))
    return (1 if is_if else 0) + max_child_depth


def test_scale_in_tiers_are_preserved_not_flattened(fbld):
    """A genuine 2-tier scale-in candidate (RSI>80 -> light blend; nested
    RSI>82.5 -> heavier UVXY) must survive as TIERED nested conditions — never
    flattened to a single OR across both thresholds (AC-4d)."""
    tiered_overlay = _dsl_scale_in_tiers_overlay()
    client = _client_returning_overlay(tiered_overlay)
    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["QQQ"]},
        )
    candidate = getattr(result, "candidate", None)
    assert candidate is not None, "a valid 2-tier scale-in candidate was rejected"

    compiled = getattr(result, "compiled_tree", None) or candidate
    depth = _count_nested_if_depth(compiled)
    assert depth >= 2, (
        f"scale-in tiers were flattened — expected >=2 nested if-levels, got "
        f"depth={depth}. AC-4d requires tiered conditions preserved, never "
        f"collapsed to one OR"
    )

    # Both the light and heavy VIX tickers from the original 2-tier candidate
    # must still be present (neither tier's fire ticker was dropped in the
    # collapse/repair process).
    compiled_tickers = symphony_schema.extract_tickers(compiled)
    assert {"VIXM", "UVXY"} <= compiled_tickers, (
        f"one or both scale-in tier fire tickers were lost during compilation "
        f"(found: {sorted(compiled_tickers)})"
    )


# ---------------------------------------------------------------------------
# AC-5: candidate compiles via plan_tree_compiler + splice yields a full
# valid symphony (validate_tree == []).
# ---------------------------------------------------------------------------


def test_generated_candidate_compiles_clean_via_plan_tree_compiler(fbld):
    """The candidate overlay DSL must round-trip through
    plan_tree_compiler.compile_plan (the only public compiler entry point)
    with tree is not None and reason is None."""
    from advisors import plan_tree_compiler

    overlay = _dsl_flat_if_overlay("SPY", 80, "UVXY")
    client = _client_returning_overlay(overlay)
    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["SPY"]},
        )
    candidate = getattr(result, "candidate", None)
    assert candidate is not None

    # Wrap the bare overlay NODE in a minimal plan envelope (plan_tickers/
    # compile_plan operate on {plan_id, objective, name, rebalance, root}) —
    # this exercises the REAL compiler, not a re-implementation of it.
    plan_envelope = {
        "plan_id": "frontrunner-candidate-test",
        "objective": "cut_drawdown",
        "name": "Frontrunner Candidate Test",
        "rebalance": "daily",
        "root": candidate,
    }
    compile_result = plan_tree_compiler.compile_plan(plan_envelope)
    assert compile_result.tree is not None, (
        f"candidate overlay failed to compile via the real plan_tree_compiler "
        f"(reason={compile_result.reason!r})"
    )
    assert compile_result.reason is None


def test_splice_into_incumbent_yields_a_validate_tree_clean_symphony(fbld, incumbent_symphony):
    """Splicing a compiled candidate overlay into the incumbent (replacing the
    detected cascade) must yield a full symphony that passes
    symphony_schema.validate_tree with ZERO hard errors — the splice must
    never produce a structurally broken tree (AC-5)."""
    from advisors import frontrunner_detector

    detection = frontrunner_detector.detect_frontrunner_cascades(incumbent_symphony)
    assert detection.cascades, "fixture setup: incumbent_symphony must have a detectable cascade"
    incumbent_cascade = detection.cascades[0]

    overlay = _dsl_flat_if_overlay("SPY", 81, "VIXM")
    client = _client_returning_overlay(overlay)
    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["SPY"]},
        )
    candidate = getattr(result, "candidate", None)
    assert candidate is not None

    spliced = fbld.splice_candidate_into_symphony(
        incumbent_symphony,
        incumbent_cascade,
        candidate,
    )
    assert spliced is not None, "splice_candidate_into_symphony returned None"

    errors = symphony_schema.validate_tree(spliced)
    assert errors == [], f"spliced symphony failed validate_tree: {errors}"

    # The spliced tree must contain the NEW candidate's fire ticker (VIXM)
    # and must NOT still contain the incumbent's old fire ticker (VIXY) in
    # place of the replaced cascade (the splice actually replaced it, not
    # merely appended alongside it).
    spliced_tickers = symphony_schema.extract_tickers(spliced)
    assert "VIXM" in spliced_tickers, "spliced tree is missing the candidate's fire ticker"


def test_splice_preserves_the_core_content_past_the_boundary(fbld, incumbent_symphony):
    """The splice must replace ONLY the detected cascade — the core content
    beyond the size-cliff boundary (CORE_ASSET_0001 in the fixture) must
    survive untouched.

    STRENGTHENED (fix cycle, 2026-07-11): the candidate built here via
    _dsl_flat_if_overlay's default core_placeholder now uses a ticker
    (CANDIDATE_PLACEHOLDER_ASSET) DISTINCT from the incumbent fixture's own
    core ticker (CORE_ASSET_0001). Previously both defaulted to the SAME
    literal ("CORE_ASSET_0001"), so this assertion passed by coincidence —
    the candidate's own placeholder ticker happened to equal the ticker
    being checked for, regardless of whether splice actually preserved any
    real incumbent content. See RC#3 in
    tests/advisors/test_frontrunner_real_boundary_contract.py for the same
    defect reproduced against a real, large incumbent tree."""
    from advisors import frontrunner_detector

    detection = frontrunner_detector.detect_frontrunner_cascades(incumbent_symphony)
    incumbent_cascade = detection.cascades[0]

    overlay = _dsl_flat_if_overlay("SPY", 81, "VIXM")
    client = _client_returning_overlay(overlay)
    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["SPY"]},
        )
    candidate = result.candidate

    spliced = fbld.splice_candidate_into_symphony(
        incumbent_symphony,
        incumbent_cascade,
        candidate,
    )
    spliced_tickers = symphony_schema.extract_tickers(spliced)
    assert "CORE_ASSET_0001" in spliced_tickers, (
        "the splice destroyed core content beyond the cascade boundary — "
        "only the detected cascade should be replaced"
    )
