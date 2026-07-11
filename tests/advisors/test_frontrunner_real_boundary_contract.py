"""RED contract tests — the REAL Fable(mocked-network-only)->compile->splice
boundary, against REAL captured fixtures (fix cycle on bf3d3b8).

WHY THIS FILE EXISTS (separate from test_frontrunner_builder.py): a real
6-symphony live run of the frontrunner pipeline proved it cannot build a
single valid candidate, yet test_frontrunner_builder.py is fully green. Root
cause: that file's own DSL fixture builders (`_dsl_flat_if_overlay` etc.)
hand-construct the NESTED `condition: {...}` shape that
`plan_tree_compiler._compile_node` actually requires — NOT the FLAT shape
(`lhs_fn`/`lhs_ticker`/`window`/`comparator`/`rhs` directly on the node, no
`condition` wrapper) that `frontrunner_builder._build_generation_prompt`
(advisors/frontrunner_builder.py:571-575) literally instructs Fable to emit,
and that real captured Fable output actually uses. The mock in the existing
file is self-fulfilling: it never exercises the real contract boundary.
This file does — only the Fable NETWORK call is mocked (same
`_build_client` patch idiom as test_frontrunner_builder.py); the compiler
and splice are always the real, unmocked functions.

FIXTURE PROVENANCE: both candidate DSL fixtures below are captured from a
real live 6-symphony frontrunner run against symphony n2ooAZTvBRN6ZzpMmWmU's
"Ballast" cascade (frtest fix-cycle diagnostic, 2026-07-11):
  - real_candidate_raw_flat_n2ooAZTvBRN6ZzpMmWmU.json: Fable's UNMODIFIED
    real output (flat condition shape, `weighting`+flat `{ticker,weight}`
    children) — this is what the CURRENT prompt actually produces.
  - real_candidate_repaired_n2ooAZTvBRN6ZzpMmWmU.json: the same candidate,
    hand-repaired to the documented nested-condition / `scheme`+`{node,pct}`
    shape plan_tree_compiler requires. Compiles clean. Used ONLY to isolate
    the RC#3 core-preservation defect (test 2 below) from the RC#1/#2
    compile-shape defect (test 1) — test 2 needs a candidate that survives
    compilation to reach the splice step at all.
real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json (already committed, previously
unused by any test) is the matching REAL incumbent symphony for the same
run — verified empirically to detect 8 cascades, cascade[0].group_name==
"Ballast", rsi_thresholds==[80.0], vix_tickers=={"UVXY","VXX"}, exactly
matching the captured run's own summary.

FIX-DIRECTION NOTE (guidance for frimpl, not enforced here): the clean fix
for RC#1/#2 is to UNIFY `_EMIT_OVERLAY_TOOL`'s declared shape (currently an
unconstrained `{"type": "object"}` — no property schema at all) and
`_build_generation_prompt`'s prompt TEXT onto the SAME nested-`condition` /
`scheme`+`{node,pct}` contract that build_plan_generator + plan_tree_compiler
already share — NOT fork plan_tree_compiler to also accept flat, and NOT
bolt on a flat->nested adapter. Notably, frontrunner_builder.py's OWN
internal ticker-walking/collapse helpers (`_walk_overlay_tickers`,
`_collect_mergeable_chain`, `_condition_signature`) ALREADY read
`node.get("condition")` (nested) — only the prompt text + the trivially
permissive tool schema disagree with the rest of the module.

TEST 1 CAVEAT (raised with team-lead, accepted): `_EMIT_OVERLAY_TOOL`'s
`input_schema` has NO shape-constraining properties to derive a candidate
from programmatically (it is a bare unconstrained object) — the flat/nested
divergence lives entirely in `_build_generation_prompt`'s free-text prose,
which is not mechanically parseable into a fixture. Test 1 below therefore
uses the REAL CAPTURED flat-shape fixture (real evidence of what Fable
currently, in fact, produces) rather than a schema-derived one. THIS FIXTURE
IS EXPECTED TO GO STALE the moment the prompt is corrected to document the
nested shape (per the fix-direction above) — at GREEN, frimpl must either
(a) re-capture a real Fable candidate under the corrected prompt and swap
this fixture, or (b) hand-author a fixture matching the new canonical nested
prompt contract and note the swap in the handoff/commit. Do not leave this
test permanently pinned to the pre-fix flat shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from advisors import frontrunner_detector, plan_tree_compiler, symphony_schema

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"


def _load_fixture(name: str) -> dict:
    with open(_FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fbld():
    """Import and return the frontrunner_builder module."""
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


@pytest.fixture(scope="module")
def real_incumbent_symphony() -> dict:
    """The real captured incumbent /score tree for symphony
    n2ooAZTvBRN6ZzpMmWmU (985KB, 6647 nodes per the captured run summary)."""
    return _load_fixture("real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json")


@pytest.fixture(scope="module")
def real_incumbent_cascade(real_incumbent_symphony):
    """Real detection result for the "Ballast" cascade in the fixture above
    — run through the REAL, unmocked detector (verified empirically to
    match the captured run's own summary: group="Ballast",
    thresholds=[80.0], vix={UVXY,VXX})."""
    detection = frontrunner_detector.detect_frontrunner_cascades(real_incumbent_symphony)
    assert detection.cascades, (
        f"fixture setup: real_tree_09 must have a detectable cascade "
        f"(skip_reason={detection.skip_reason!r})"
    )
    return detection.cascades[0]


def _tool_use_block(input_payload: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_payload
    return block


def _client_returning_overlay(overlay_dsl: dict) -> MagicMock:
    """Mirrors test_frontrunner_builder.py's mocking idiom exactly — mocks
    ONLY the Fable network call, never the compiler or the splice."""
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [_tool_use_block({"overlay": overlay_dsl})]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _patch_fable_client(fbld_module, client: MagicMock):
    return patch.object(fbld_module, "_build_client", return_value=client)


# ---------------------------------------------------------------------------
# Test 1 — RC#1/#2 guard: realistic (real-captured) Fable candidate must
# round-trip through the REAL compile_plan + REAL splice.
# ---------------------------------------------------------------------------


def test_realistic_fable_shaped_candidate_round_trips_generate_compile_splice(
    fbld, real_incumbent_symphony, real_incumbent_cascade
):
    """A candidate shaped exactly as real Fable output currently is (flat
    condition, `weighting`+flat weight children — see fixture provenance in
    the module docstring) must compile via the REAL plan_tree_compiler and
    splice via the REAL splice_candidate_into_symphony into a REAL incumbent
    tree. Only the Fable NETWORK call is mocked."""
    raw_overlay = _load_fixture("real_candidate_raw_flat_n2ooAZTvBRN6ZzpMmWmU.json")
    client = _client_returning_overlay(raw_overlay)

    with _patch_fable_client(fbld, client):
        result = fbld.generate_candidate_overlay(
            signal_context={"watched_tickers": ["SPY"]},
        )

    assert result.candidate is not None, (
        "a real-shaped candidate with a genuine VIX-family fire ticker "
        "(UVXY/VIXM) was rejected before it even reached compilation"
    )
    assert result.compiled_tree is not None, (
        "RC#1/#2: the candidate shape Fable's own prompt "
        "(_build_generation_prompt) instructs it to emit does not compile "
        "via the real plan_tree_compiler.compile_plan — the prompt's "
        "documented DSL contract and the compiler's actual contract have "
        "diverged (compile_plan silently returned tree=None; "
        "generate_candidate_overlay does not propagate that as .error)"
    )

    spliced = fbld.splice_candidate_into_symphony(
        real_incumbent_symphony,
        real_incumbent_cascade,
        result.candidate,
    )
    assert spliced is not None, (
        "RC#1/#2: splice_candidate_into_symphony's internal recompile of "
        "the real-shaped candidate failed the same way compile_plan did "
        "above — the round trip from a realistic Fable candidate to a "
        "spliced symphony is broken end to end"
    )


# ---------------------------------------------------------------------------
# Test 2 — RC#3 guard: splice must not discard the incumbent's real core.
# Uses the REPAIRED (documented-shape, compiles clean) candidate fixture so
# this test isolates RC#3 from the RC#1/#2 compile-shape defect above.
# ---------------------------------------------------------------------------


def test_splice_preserves_incumbent_core_tickers_beyond_the_cascade_boundary(
    fbld, real_incumbent_symphony, real_incumbent_cascade
):
    """Splicing a candidate into a REAL, large incumbent symphony must
    preserve every incumbent ticker OUTSIDE the detected cascade boundary
    (the incumbent's real core strategy) and must not leak the candidate's
    own continuation placeholder ticker into the result (RC#3). Verified
    empirically against this exact fixture pair: the incumbent has 941
    tickers outside the detected cascade; on bf3d3b8, 207 of them go
    missing post-splice."""
    repaired_overlay = _load_fixture("real_candidate_repaired_n2ooAZTvBRN6ZzpMmWmU.json")

    # Sanity: this fixture is expected to compile clean (isolates RC#3).
    plan_envelope = {
        "plan_id": "frontrunner-rc3-isolation-test",
        "objective": "cut_drawdown",
        "name": "RC3 isolation candidate",
        "rebalance": "daily",
        "root": repaired_overlay,
    }
    compile_result = plan_tree_compiler.compile_plan(plan_envelope)
    assert compile_result.tree is not None, (
        f"fixture setup: the REPAIRED (documented-shape) candidate must "
        f"compile clean so this test isolates RC#3 from RC#1/#2 "
        f"(reason={compile_result.reason!r})"
    )

    spliced = fbld.splice_candidate_into_symphony(
        real_incumbent_symphony,
        real_incumbent_cascade,
        repaired_overlay,
    )
    assert spliced is not None, "splice_candidate_into_symphony returned None unexpectedly"

    incumbent_tickers = symphony_schema.extract_tickers(real_incumbent_symphony)
    cascade_tickers = symphony_schema.extract_tickers(real_incumbent_cascade.overlay_tree)
    core_tickers = incumbent_tickers - cascade_tickers
    spliced_tickers = symphony_schema.extract_tickers(spliced)

    missing_core_tickers = core_tickers - spliced_tickers
    assert not missing_core_tickers, (
        f"RC#3: splice_candidate_into_symphony discarded "
        f"{len(missing_core_tickers)} of the incumbent's {len(core_tickers)} "
        f"real core tickers (outside the detected cascade boundary) — the "
        f"whole detected cascade if-node (condition + real fire branch + "
        f"real continuation/core branch) was replaced by the candidate's "
        f"compiled node, whose own else branch is only a placeholder stub. "
        f"No step grafts the incumbent's real core back into the "
        f"candidate's placeholder slot. Sample missing: "
        f"{sorted(missing_core_tickers)[:10]}"
    )

    assert "CORE_STRATEGY_PLACEHOLDER" not in spliced_tickers, (
        "RC#3: the candidate's own continuation placeholder ticker "
        "(CORE_STRATEGY_PLACEHOLDER) leaked into the spliced symphony in "
        "place of the incumbent's real core content"
    )
