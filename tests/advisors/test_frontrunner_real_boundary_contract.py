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

FIXTURE PROVENANCE: real_candidate_repaired_n2ooAZTvBRN6ZzpMmWmU.json is
captured from a real live 6-symphony frontrunner run against symphony
n2ooAZTvBRN6ZzpMmWmU's "Ballast" cascade (frtest fix-cycle diagnostic,
2026-07-11) — the same real candidate (SPY RSI(10) overbought cascade ->
tiered UVXY/VIXM hedge), authored in the nested `condition:{...}` /
`scheme`+`{node,pct}` shape. real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json (already
committed, previously unused by any test) is the matching REAL incumbent
symphony for the same run — verified empirically to detect 8 cascades,
cascade[0].group_name=="Ballast", rsi_thresholds==[80.0],
vix_tickers=={"UVXY","VXX"}, exactly matching the captured run's own
summary.

RC#1/#2 FIX (landed, commit 519d16a): `_EMIT_OVERLAY_TOOL`'s schema and
`_build_generation_prompt`'s prose were unified onto the nested-`condition`
/ `scheme`+`{node,pct}` contract that build_plan_generator +
plan_tree_compiler already required and this module's own internal
ticker-walking/collapse helpers already assumed. `generate_candidate_overlay`
no longer reports a failed compile as a silent success — it retries and
surfaces `compile_result.reason` on exhaustion.

TEST 1 FIXTURE MIGRATION (frtest, post-GREEN, 2026-07-11): the original
`real_candidate_raw_flat_n2ooAZTvBRN6ZzpMmWmU.json` fixture (Fable's real
pre-fix output, flat shape) is DELETED — it went stale the moment the fix
landed, exactly as flagged in the RED commit. `_EMIT_OVERLAY_TOOL`'s
`input_schema` still has no MECHANICALLY-derivable single canonical shape to
generate a fixture from (it's a JSON-schema description, not a strict
one-shape grammar), so test 1 below re-uses
real_candidate_repaired_n2ooAZTvBRN6ZzpMmWmU.json directly — hand-verified
line-by-line against the new schema's declared `properties`/`required`
(`kind`, `condition` nested with `lhs_fn`/`lhs_ticker`/`window`/
`comparator`/`rhs`, `then`/`else` arrays, weight nodes with `scheme`+
`{node,pct}` children) and against `_build_generation_prompt`'s new
`_EXAMPLE_OVERLAY`: shape-identical. This is now the single canonical
candidate shape post-fix, so both tests sharing one fixture file is
intentional, not laziness — a future re-divergence between "what test 1
exercises" and "what test 2 exercises" would itself be a signal worth
investigating.
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
    """A candidate shaped per the current (post-fix) contract — nested
    `condition:{...}`, `scheme`+`{node,pct}` weight children, see fixture
    provenance in the module docstring — must compile via the REAL
    plan_tree_compiler and splice via the REAL splice_candidate_into_symphony
    into a REAL incumbent tree. Only the Fable NETWORK call is mocked."""
    raw_overlay = _load_fixture("real_candidate_repaired_n2ooAZTvBRN6ZzpMmWmU.json")
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


def _find_node_by_id(tree: dict, target_id: str):
    """Iterative DFS returning the node with id==target_id, or None.

    Test-owned traversal, deliberately independent of
    frontrunner_builder._find_node_by_id — this test's ground truth for
    "which incumbent node is the real cascade root" must not be coupled to
    the implementation under test."""
    if not isinstance(tree, dict):
        return None
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("id") == target_id:
            return node
        for child in node.get("children") or []:
            stack.append(child)
    return None


def test_splice_preserves_incumbent_core_tickers_beyond_the_cascade_boundary(
    fbld, real_incumbent_symphony, real_incumbent_cascade
):
    """Splicing a candidate into a REAL, large incumbent symphony must
    preserve every incumbent ticker OUTSIDE the cascade's real fire branch
    (the incumbent's real core strategy) and must not leak the candidate's
    own continuation placeholder ticker into the result (RC#3). Verified
    empirically against this exact fixture pair: the incumbent has 931
    tickers outside the real fire branch; a whole-node replace with no
    graft (the pre-fix behavior) loses 197 of them.

    NOTE on "outside the real fire branch" (frtest, post-GREEN diagnostic,
    2026-07-11): this is deliberately computed from the REAL cascade root
    node (found by id in the untouched incumbent tree) and its
    is-else-condition?=False (fire) if-child — NOT from
    `real_incumbent_cascade.overlay_tree`. That field is
    frontrunner_detector's own COMPACT/STUBBED reporting copy: it further
    stubs nested internal-hedge sub-gates found WITHIN the fire branch
    itself (this exact fixture has a self-referential VIXY-timing
    sub-structure nested inside the cascade's own hedge logic), so
    subtracting the stub's tickers from the incumbent's full set wrongly
    counted 10 fire-branch-only tickers (CORE_ASSET_0001..0010, despite the
    misleading "CORE_ASSET" naming — they live exclusively inside the fire
    branch that the candidate is SUPPOSED to replace, not the
    else/continuation branch the graft preserves) as "core" that must
    survive. Diagnosed by frimpl during the GREEN cycle; verified
    independently here by resolving the real fire branch directly before
    adopting the fix — do not revert to `overlay_tree`-based subtraction."""
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

    # Resolve the REAL cascade root node (untouched incumbent tree, found by
    # id) and its real fire (is-else-condition?=False) branch — see the
    # docstring above for why this must NOT be derived from
    # real_incumbent_cascade.overlay_tree.
    real_cascade_root = _find_node_by_id(
        real_incumbent_symphony, real_incumbent_cascade.overlay_tree.get("id")
    )
    assert real_cascade_root is not None, (
        "fixture setup: could not locate the real cascade root node by id "
        "in the incumbent tree"
    )
    real_fire_branch = next(
        (
            c
            for c in real_cascade_root.get("children") or []
            if isinstance(c, dict) and c.get("is-else-condition?") is False
        ),
        None,
    )
    assert real_fire_branch is not None, (
        "fixture setup: could not locate the real cascade's fire "
        "(is-else-condition?=False) branch"
    )

    incumbent_tickers = symphony_schema.extract_tickers(real_incumbent_symphony)
    real_fire_tickers = symphony_schema.extract_tickers(real_fire_branch)
    core_tickers = incumbent_tickers - real_fire_tickers
    spliced_tickers = symphony_schema.extract_tickers(spliced)

    missing_core_tickers = core_tickers - spliced_tickers
    assert not missing_core_tickers, (
        f"RC#3: splice_candidate_into_symphony discarded "
        f"{len(missing_core_tickers)} of the incumbent's {len(core_tickers)} "
        f"real core tickers (everything outside the cascade's real fire "
        f"branch) — the whole detected cascade if-node (condition + real "
        f"fire branch + real continuation/core branch) was replaced by the "
        f"candidate's compiled node without grafting the incumbent's real "
        f"continuation/core content into the candidate's placeholder slot. "
        f"Sample missing: {sorted(missing_core_tickers)[:10]}"
    )

    assert "CORE_STRATEGY_PLACEHOLDER" not in spliced_tickers, (
        "RC#3: the candidate's own continuation placeholder ticker "
        "(CORE_STRATEGY_PLACEHOLDER) leaked into the spliced symphony in "
        "place of the incumbent's real core content"
    )
