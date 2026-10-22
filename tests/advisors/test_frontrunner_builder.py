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
  - Model: the suggestion-family (Fable line) model that model_config resolves, sent
    through llm_request.send, over the Anthropic SDK, mirroring
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
import importlib.util
import logging
import pathlib
import re
from unittest.mock import MagicMock, patch

import httpx
import pytest

from advisors import symphony_schema
from tests._anthropic_http_fake import FIXTURES_DIR, AnthropicHttpFake, load_fixture
from tests.advisors.test_frontrunner_gate_wiring import _make_shaped_result
from tests.llm_request.conftest import CAPTURED, error_outcome, message_body, newest_id

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


# ---------------------------------------------------------------------------
# Overlay requests over the Anthropic HTTP fake.
#
# The generator is driven with a real anthropic client whose transport is the
# in-process fake, so every claim about the request (which model it names, what
# parameters it carries, whether a fallback was asked for) is read off the
# outgoing HTTP body, and the SDK's own reply parsing runs on the reply.
#
# Patch targets and forms production must keep:
#   - advisors.frontrunner_builder._build_client() is the client seam (module
#     attribute, called with no arguments once per generate_candidate_overlay call).
#   - the model comes from llm_request.send / model_config (SUGGESTION knob);
#     no model identifier literal and no FABLE_MODEL attribute remain in the module.
#   - the tool definition sent is the module attribute _EMIT_OVERLAY_TOOL, unchanged.
#   - the module logger is "advisors.frontrunner_builder"; the generation-failure
#     record in _run_build_for_symphony is emitted at WARNING or above.
#
# Fixtures: model pages and replies are the captured and schema-derived records under
# tests/fixtures/anthropic. The two overlay variants below are runtime-derived from
# them (provenance in _overlay_variant), because the captured tool replies for a
# refusal or a fallback were recorded for the swap tool, not the overlay tool.
# ---------------------------------------------------------------------------

_ENV_SUGGESTION_MODEL = "ADVISOR_SUGGESTION_MODEL"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_LEGACY_SAMPLING_PARAMETERS = ("temperature", "top_p", "top_k", "thinking")
# Thinking counts against max_tokens on these models, so the budget must hold a thinking
# reply at high effort; anthropic 0.85.0 refuses a non-streaming request above 21,333
# output tokens when no timeout is passed, and this site passes none.
_MIN_OVERLAY_OUTPUT_TOKENS = 16000
_MAX_UNSTREAMED_OUTPUT_TOKENS = 21333
_QUEUED_LISTING_FAILURES = 8
_OVERLAY_JOB_SYMPHONY_ID = "overlay-job-symphony"
_CAPTURED_OVERLAY_REPLY = "thinking_tool_emit_frontrunner_overlay.json"
_VERSIONED_MODEL_ID = re.compile(r"claude-(?:opus|sonnet|haiku|fable|mythos)-\d")


def _schema_validator():
    spec = importlib.util.spec_from_file_location(
        "anthropic_fixture_schema_check_overlay", FIXTURES_DIR / "schema_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _captured_overlay_block() -> dict:
    return next(
        block
        for block in message_body(_CAPTURED_OVERLAY_REPLY)["content"]
        if block["type"] == "tool_use"
    )


_CAPTURED_OVERLAY = _captured_overlay_block()["input"]["overlay"]


def _overlay_variant(envelope: str, overlay: dict | None = None) -> dict:
    """A reply envelope whose tool_use block is the overlay tool's.

    Derived from two real records: the envelope (stop_reason, stop_details, fallback and
    thinking blocks, usage, top-level model, tool_use id) is the named message fixture,
    and the one edit is that its tool_use block takes the name and input of the captured
    overlay reply (or of ``overlay`` when given, for the no-VIX case). The refusal and
    fallback fixtures were recorded for the swap tool, which this site never offers.
    The result must pass the public-reference validator, so the derivation cannot
    produce a shape the real API would not.
    """
    body = copy.deepcopy(message_body(envelope))
    captured = _captured_overlay_block()
    for block in body["content"]:
        if block["type"] == "tool_use":
            block["name"] = captured["name"]
            block["input"] = {"overlay": copy.deepcopy(overlay or _CAPTURED_OVERLAY)}
    assert _schema_validator().check_message_file(body, envelope) == []
    return body


@pytest.fixture
def overlay_fake() -> AnthropicHttpFake:
    """The in-process Anthropic API serving the captured Models page."""
    return AnthropicHttpFake.with_captured_page()


def _generate_overlay(fbld, fake, monkeypatch, *bodies, **kwargs):
    for body in bodies:
        fake.queue_message(body)
    monkeypatch.setattr(fbld, "_build_client", lambda: fake.client)
    return fbld.generate_candidate_overlay({"watched_tickers": ["SPY"]}, **kwargs)


def _models_named(fake) -> list[str]:
    return [request.body["model"] for request in fake.message_requests]


def _generation_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == "advisors.frontrunner_builder"
    ]


def _walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _non_fable_served_id() -> str:
    """A model the captured listing serves that is neither the newest nor any fable-line one."""
    return next(r["id"] for r in CAPTURED["data"] if r.get("line") != "fable")


def _unresolved_fake(kind: str) -> AnthropicHttpFake:
    if kind == "no-fable-model":
        return AnthropicHttpFake(pages=[load_fixture("models/no_fable.json")])
    fake = AnthropicHttpFake()
    for _ in range(_QUEUED_LISTING_FAILURES):
        fake.queue_listing_outcome(
            error_outcome("503_unavailable.json")
            if kind == "listing-503"
            else httpx.ConnectError("no route to host")
        )
    return fake


_UNRESOLVED_KINDS = ["listing-503", "listing-connect-error", "no-fable-model"]
_LISTING_FAILURE_TEXTS = ("Service temporarily unavailable", "no route to host")


class TestOverlayModelResolution:
    def test_overlay_request_names_the_newest_active_fable_line_model(
        self, fbld, overlay_fake, monkeypatch
    ):
        result = _generate_overlay(
            fbld, overlay_fake, monkeypatch, message_body(_CAPTURED_OVERLAY_REPLY)
        )

        assert result.error is None and result.candidate is not None
        assert _models_named(overlay_fake) == [newest_id("fable")], (
            "the overlay request must name the newest active fable-line model of the "
            "Models API listing, not a model fixed in source"
        )
        assert overlay_fake.misuse == []

    def test_the_fixed_model_constant_is_gone(self, fbld):
        assert not hasattr(fbld, "FABLE_MODEL")

    def test_module_source_names_no_versioned_model_id(self, fbld):
        source = pathlib.Path(fbld.__file__).read_text(encoding="utf-8")

        assert _VERSIONED_MODEL_ID.findall(source) == []

    def test_override_names_exactly_the_served_id_and_needs_no_listing(
        self, fbld, overlay_fake, monkeypatch
    ):
        override = _non_fable_served_id()
        monkeypatch.setenv(_ENV_SUGGESTION_MODEL, override)

        result = _generate_overlay(
            fbld, overlay_fake, monkeypatch, message_body(_CAPTURED_OVERLAY_REPLY)
        )

        assert result.error is None and result.candidate is not None
        assert _models_named(overlay_fake) == [override]
        assert overlay_fake.listing_requests == []

    @pytest.mark.parametrize(
        ("rejection", "code"),
        [
            pytest.param("404_not_found.json", "override_model_not_found", id="not-found"),
            pytest.param(
                "400_invalid_request.json", "override_request_rejected", id="request-rejected"
            ),
        ],
    )
    def test_rejected_override_degrades_naming_the_variable_without_substituting_a_model(
        self, fbld, overlay_fake, monkeypatch, rejection, code
    ):
        override = _non_fable_served_id()
        monkeypatch.setenv(_ENV_SUGGESTION_MODEL, override)
        bodies = [error_outcome(rejection)] * fbld.MAX_GENERATION_ATTEMPTS

        result = _generate_overlay(fbld, overlay_fake, monkeypatch, *bodies)

        assert result.candidate is None
        assert code in result.error and _ENV_SUGGESTION_MODEL in result.error
        assert set(_models_named(overlay_fake)) == {override}, (
            "a rejected override must never be replaced by the newest listed model"
        )

    @pytest.mark.parametrize("kind", _UNRESOLVED_KINDS)
    def test_unresolved_family_sends_no_request_and_names_model_unavailable_only(
        self, fbld, monkeypatch, unresolved_models, kind
    ):
        fake = _unresolved_fake(kind)

        result = _generate_overlay(fbld, fake, monkeypatch)

        assert fake.message_requests == [], "an unresolved family must send no Messages request"
        assert result.candidate is None
        assert "model_unavailable" in result.error
        assert "claude-" not in result.error
        assert not any(text in result.error for text in _LISTING_FAILURE_TEXTS)

    def test_a_rejected_request_to_a_resolved_model_degrades_with_the_exception_type_only(
        self, fbld, overlay_fake, monkeypatch
    ):
        rejection = error_outcome("400_invalid_request.json")

        result = _generate_overlay(fbld, overlay_fake, monkeypatch, rejection)

        assert result.candidate is None
        assert result.error == "BadRequestError"
        assert _models_named(overlay_fake) == [newest_id("fable")]


class TestOverlayRequestShape:
    def test_every_attempt_asks_for_a_fallback_model(self, fbld, overlay_fake, monkeypatch):
        _generate_overlay(
            fbld,
            overlay_fake,
            monkeypatch,
            message_body("text_only_with_tools.json"),
            message_body(_CAPTURED_OVERLAY_REPLY),
        )

        assert len(overlay_fake.message_requests) == 2
        for request in overlay_fake.message_requests:
            assert request.body["fallbacks"] == "default"
            assert _FALLBACK_BETA in request.headers.get("anthropic-beta", "")

    def test_every_attempt_sets_effort_and_omits_legacy_sampling_parameters(
        self, fbld, overlay_fake, monkeypatch
    ):
        _generate_overlay(
            fbld,
            overlay_fake,
            monkeypatch,
            message_body("text_only_with_tools.json"),
            message_body(_CAPTURED_OVERLAY_REPLY),
        )

        assert len(overlay_fake.message_requests) == 2
        for request in overlay_fake.message_requests:
            body = request.body
            assert body["output_config"]["effort"] == "high"
            for name in _LEGACY_SAMPLING_PARAMETERS:
                assert name not in body, f"{name} must not be sent to a thinking model"
            assert [m["role"] for m in body["messages"]] == ["user"], (
                "no assistant prefill: the last message must be the user's"
            )

    def test_output_budget_holds_a_thinking_reply_and_the_sdk_accepts_it(
        self, fbld, overlay_fake, monkeypatch
    ):
        result = _generate_overlay(
            fbld, overlay_fake, monkeypatch, message_body(_CAPTURED_OVERLAY_REPLY)
        )

        assert result.error is None, "the SDK refused or the call failed before a reply"
        budget = overlay_fake.message_requests[0].body["max_tokens"]
        assert _MIN_OVERLAY_OUTPUT_TOKENS <= budget <= _MAX_UNSTREAMED_OUTPUT_TOKENS

    def test_tool_definition_is_sent_non_strict_with_open_condition_and_rhs(
        self, fbld, overlay_fake, monkeypatch
    ):
        _generate_overlay(fbld, overlay_fake, monkeypatch, message_body(_CAPTURED_OVERLAY_REPLY))

        sent = overlay_fake.message_requests[0].body["tools"]
        assert sent == [fbld._EMIT_OVERLAY_TOOL], "the overlay tool must be sent unchanged"
        for node in _walk(sent):
            if isinstance(node, dict):
                assert "strict" not in node
                assert node.get("additionalProperties") is not False
        condition = sent[0]["input_schema"]["properties"]["overlay"]["properties"]["condition"]
        assert condition["properties"]["rhs"] == {"type": "object"}

    def test_tool_input_is_still_validated_for_a_vix_ticker(self, fbld, overlay_fake, monkeypatch):
        no_vix = _overlay_variant(_CAPTURED_OVERLAY_REPLY, _dsl_no_vix_overlay())

        result = _generate_overlay(
            fbld, overlay_fake, monkeypatch, *[no_vix] * fbld.MAX_GENERATION_ATTEMPTS
        )

        assert result.candidate is None and result.compiled_tree is None
        assert "VIX" in result.error
        assert len(overlay_fake.message_requests) == fbld.MAX_GENERATION_ATTEMPTS
        for request in overlay_fake.message_requests:
            assert request.body["tool_choice"] == {"type": "auto"}


class TestOverlayReplyHandling:
    def test_text_only_replies_stop_after_three_requests_with_the_no_tool_call_reason(
        self, fbld, overlay_fake, monkeypatch
    ):
        text_only = message_body("text_only_with_tools.json")

        result = _generate_overlay(
            fbld, overlay_fake, monkeypatch, *[text_only] * fbld.MAX_GENERATION_ATTEMPTS
        )

        assert len(overlay_fake.message_requests) == 3
        assert result.candidate is None
        assert "no_tool_call" in result.error and "NoToolUseBlock" not in result.error

    def test_a_tool_call_on_the_second_reply_is_used(self, fbld, overlay_fake, monkeypatch):
        result = _generate_overlay(
            fbld,
            overlay_fake,
            monkeypatch,
            message_body("text_only_with_tools.json"),
            message_body(_CAPTURED_OVERLAY_REPLY),
        )

        assert len(overlay_fake.message_requests) == 2
        assert result.error is None
        assert result.candidate == _CAPTURED_OVERLAY

    def test_truncation_and_missing_tool_calls_share_the_attempt_bound(
        self, fbld, overlay_fake, monkeypatch
    ):
        result = _generate_overlay(
            fbld,
            overlay_fake,
            monkeypatch,
            message_body("max_tokens.json"),
            message_body("text_only_with_tools.json"),
            message_body(_CAPTURED_OVERLAY_REPLY),
        )

        assert len(overlay_fake.message_requests) == 3
        assert result.error is None
        assert result.candidate == _CAPTURED_OVERLAY

    @pytest.mark.parametrize("truncated", ["max_tokens.json", "max_tokens_in_thinking.json"])
    def test_truncated_replies_retry_up_to_the_bound_then_name_max_tokens(
        self, fbld, overlay_fake, monkeypatch, truncated
    ):
        result = _generate_overlay(
            fbld,
            overlay_fake,
            monkeypatch,
            *[message_body(truncated)] * fbld.MAX_GENERATION_ATTEMPTS,
        )

        assert len(overlay_fake.message_requests) == fbld.MAX_GENERATION_ATTEMPTS
        assert result.candidate is None and "max_tokens" in result.error
        for request in overlay_fake.message_requests:
            assert request.body["fallbacks"] == "default", "each retry asks for a fallback too"
            assert request.body["output_config"]["effort"] == "high"

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param(lambda: message_body("refusal_reasoning_extraction.json"), id="category"),
            pytest.param(lambda: message_body("refusal_null_category.json"), id="null-category"),
            pytest.param(lambda: message_body("refusal_after_partial_text.json"), id="after-text"),
            pytest.param(
                lambda: _overlay_variant("refusal_after_partial_tool.json"), id="after-tool"
            ),
        ],
    )
    def test_a_refusal_stops_the_loop_keeps_no_partial_output_and_names_the_category(
        self, fbld, overlay_fake, monkeypatch, reply
    ):
        refusal = reply()

        result = _generate_overlay(fbld, overlay_fake, monkeypatch, refusal)

        assert len(overlay_fake.message_requests) == 1, (
            "a refusal is not retried on the same model and spends no further attempts"
        )
        assert overlay_fake.misuse == []
        assert result.candidate is None and result.compiled_tree is None
        assert "refusal" in result.error
        category = refusal["stop_details"]["category"]
        if category is None:
            assert "None" not in result.error and "null" not in result.error
        else:
            assert category in result.error

    def test_a_reply_served_by_a_fallback_model_supplies_the_overlay(
        self, fbld, overlay_fake, monkeypatch
    ):
        served = _overlay_variant("fallback_served_tool.json")

        result = _generate_overlay(fbld, overlay_fake, monkeypatch, served)

        assert result.error is None
        assert result.candidate == _CAPTURED_OVERLAY
        assert len(overlay_fake.message_requests) == 1
        assert overlay_fake.message_requests[0].body["fallbacks"] == "default"


@pytest.fixture
def overlay_job(incumbent_symphony):
    """The leaves of one run_frontrunner_build pass, patched where they originate.

    Roster, Composer score fetch, Atlas reads, the two backtests and the proposal write
    are patched at their origin modules so the real detector, generator, splice, gate
    and acceptance run. The incumbent backtest comes first and the candidate's second,
    with shapes whose gate and Calmar outcome is established by the gate-wiring suite.
    Yields the proposal-insert mock.
    """
    incumbent_result = _make_shaped_result(
        [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02], n_days=100
    )
    candidate_result = _make_shaped_result(
        [0.60, 0.60, 0.60, 0.60, -0.05, 0.60, 0.60, 0.60, 0.60, -0.05], n_days=100
    )
    calls = {"n": 0}

    def _backtest(*_args, **_kwargs):
        calls["n"] += 1
        return incumbent_result if calls["n"] == 1 else candidate_result

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.community_strats.load_community_strategies",
            return_value={
                "available": False,
                "candidates": [],
                "stats": {},
                "source": "captplanet",
            },
        ),
        patch(
            "advisors.frontrunner_signals.load_frontrunner_signals",
            return_value={
                "available": False,
                "reason": "TestGuardNoLiveAtlas",
                "signals": [],
                "stats": {},
                "source": "captplanet",
            },
        ),
        patch("advisors.composer_backtest_client.run_backtest", side_effect=_backtest),
        patch("database.insert_frontrunner_proposal") as insert,
    ):
        yield insert


def _run_job(fbld, fake, monkeypatch, *bodies):
    for body in bodies:
        fake.queue_message(body)
    monkeypatch.setattr(fbld, "_build_client", lambda: fake.client)
    fbld.run_frontrunner_build([_OVERLAY_JOB_SYMPHONY_ID])


class TestFrontrunnerRunOverlayJob:
    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param(lambda: message_body(_CAPTURED_OVERLAY_REPLY), id="primary-model-reply"),
            pytest.param(
                lambda: _overlay_variant("fallback_served_tool.json"), id="fallback-model-reply"
            ),
        ],
    )
    def test_run_queues_the_overlay_taken_from_the_reply_on_the_resolved_model(
        self, fbld, overlay_fake, overlay_job, monkeypatch, reply
    ):
        _run_job(fbld, overlay_fake, monkeypatch, reply())

        assert overlay_job.call_count == 1, "the overlay from the reply must reach the queue"
        metrics = overlay_job.call_args.kwargs["metrics_json"]
        assert metrics["overlay_tree"] == _CAPTURED_OVERLAY
        request = overlay_fake.message_requests[0]
        assert [r.body["model"] for r in overlay_fake.message_requests] == [newest_id("fable")]
        assert request.body["fallbacks"] == "default"
        assert request.body["tool_choice"] == {"type": "auto"}
        assert request.body["output_config"]["effort"] == "high"

    def test_override_model_is_named_by_every_overlay_request_of_a_run(
        self, fbld, overlay_fake, overlay_job, monkeypatch
    ):
        override = _non_fable_served_id()
        monkeypatch.setenv(_ENV_SUGGESTION_MODEL, override)

        _run_job(
            fbld,
            overlay_fake,
            monkeypatch,
            message_body("text_only_with_tools.json"),
            message_body(_CAPTURED_OVERLAY_REPLY),
        )

        assert _models_named(overlay_fake) == [override, override]
        assert overlay_job.call_count == 1

    @pytest.mark.parametrize("kind", _UNRESOLVED_KINDS)
    def test_unresolved_family_queues_nothing_and_logs_model_unavailable_at_warning(
        self, fbld, overlay_job, monkeypatch, unresolved_models, caplog, kind
    ):
        fake = _unresolved_fake(kind)

        with caplog.at_level(logging.WARNING, logger="advisors.frontrunner_builder"):
            _run_job(fbld, fake, monkeypatch)

        assert fake.message_requests == []
        overlay_job.assert_not_called()
        warnings = _generation_warnings(caplog)
        assert any("model_unavailable" in text for text in warnings), warnings
        shown = " ".join(warnings)
        assert "claude-" not in shown
        assert not any(text in shown for text in _LISTING_FAILURE_TEXTS)

    def test_replies_without_a_tool_call_queue_nothing_and_log_no_tool_call_at_warning(
        self, fbld, overlay_fake, overlay_job, monkeypatch, caplog
    ):
        text_only = message_body("text_only_with_tools.json")

        with caplog.at_level(logging.WARNING, logger="advisors.frontrunner_builder"):
            _run_job(fbld, overlay_fake, monkeypatch, *[text_only] * fbld.MAX_GENERATION_ATTEMPTS)

        assert len(overlay_fake.message_requests) == 3
        overlay_job.assert_not_called()
        warnings = _generation_warnings(caplog)
        assert any("no_tool_call" in text for text in warnings), warnings
        assert not any("NoToolUseBlock" in text for text in warnings)

    def test_a_refusal_queues_nothing_and_logs_the_reason_at_warning(
        self, fbld, overlay_fake, overlay_job, monkeypatch, caplog
    ):
        refusal = _overlay_variant("refusal_after_partial_tool.json")

        with caplog.at_level(logging.WARNING, logger="advisors.frontrunner_builder"):
            _run_job(fbld, overlay_fake, monkeypatch, refusal)

        assert len(overlay_fake.message_requests) == 1
        overlay_job.assert_not_called()
        warnings = _generation_warnings(caplog)
        assert any(
            "refusal" in text and refusal["stop_details"]["category"] in text for text in warnings
        ), warnings
