"""RED tests — FRONTRUNNER generation-quality remediation (feature-plans/
advisor-generation-quality.md). Module under test: advisors/frontrunner_builder.py.

SCOPE (this cycle, team-lead dispatch 2026-07-11): the FRONTRUNNER generator
ONLY. Strategy Builder (advisors/build_plan_generator.py) is a separate
follow-on cycle and is NOT touched here.

WHY THIS FILE EXISTS: PM-verified diagnostics (frquality/frsurvey, THREAD H,
.claude/PM-ACTIVE-WORK.md) proved the live frontrunner generator produces
"rudimentary garbage" — a single flat 2-tier RSI cascade, Atlas fed as 4
inert scalars, every symphony getting an identical generic prompt. This
batch pins the deterministic prompt/plumbing contract for four defect
clusters, all RED against 9ba71e1 and GREEN only once fixed:

  1. AC-1a — the prompt must proactively INVITE compound/multi-signal design
     (not just describe a single-signal RSI-overbought cascade + mention
     if_compound as bare grammar).
  2. AC-1b — the prompt must embed >=1 COMPILER-VERIFIED compound (if_compound,
     condition type binary_compound/compound) worked exemplar, not only the
     existing flat 2-tier _EXAMPLE_OVERLAY.
  3. AC-4/AC-5 (frontrunner half) — the Atlas signal fed into the prompt must
     carry real STRUCTURAL exemplars (nesting/tier signal), not the current
     4-scalar {vix_tickers, rsi_thresholds, watched_tickers, basket_node_count}
     fingerprint; near-identical Atlas candidates must not surface as N raw
     near-duplicate fingerprints; a materially different (structured vs.
     absent) Atlas input must produce a materially different prompt.
  4. AC-8 — the `"kind" in cascade.overlay_tree` guard (frontrunner_builder.py
     ~:1185, ~:1290) is permanently False because frontrunner_detector's
     overlay_tree is Composer step-keyed, never kind-keyed — watched_tickers
     is unconditionally [] for every real symphony, so every symphony gets the
     identical "(none supplied)" prompt. Fixed shape must use a step-aware
     collector (frontrunner_detector._collect_tickers) so watched_tickers
     populates and two distinct incumbents produce two distinct prompts.

CONTRACT SOURCE: feature-plans/advisor-generation-quality.md AC-1, AC-4, AC-5,
AC-8 (frontrunner-scoped subset only — AC-2/3/6/7 are Strategy Builder, AC-9
is atlas_cache, both out of scope for this batch).

ADVERSARIAL FOCUS: every assertion is on SHAPE/STRUCTURE/PRESENCE/TOKEN-COUNT
against the real prompt-builder + real plan_tree_compiler + real
frontrunner_detector — never a hardcoded producer value. Structural-token
assertions ("IF"/"WHEN") are grounded by directly proving, in-test, that
symphony_schema.render_rules_text emits exactly those tokens for a genuinely
nested cascade — not guessed vocabulary.

FIXTURE PROVENANCE: every tree is built via the real symphony_schema
constructors (schema-derived), reusing the exact detectable RSI-cascade shape
already proven in test_frontrunner_atlas_patterns.py / test_frontrunner_builder.py
(RSI(ticker) gt threshold -> VIX-family fire branch). No live Atlas/Fable/
Composer call is made anywhere in this file.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from advisors import plan_tree_compiler, symphony_schema

# ---------------------------------------------------------------------------
# Module-under-test import guard.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


# ---------------------------------------------------------------------------
# Shared fixture builders — real symphony_schema constructors only.
# ---------------------------------------------------------------------------


def _make_frontrunner_shaped_tree(name: str = "Flat Frontrunner Candidate") -> dict:
    """A single-rung RSI(QQQ) gt 81 -> UVXY cascade — the same minimal
    detectable shape used across test_frontrunner_atlas_patterns.py /
    test_frontrunner_builder.py, reused here so the real detector reliably
    finds it without re-deriving the size-cliff/VIX-ticker signature."""
    cascade = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "QQQ", window=10),
            "gt",
            81,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("UVXY")])],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    return symphony_schema.make_root(name, "daily", [cascade])


def _make_two_tier_frontrunner_shaped_tree(name: str = "Tiered Frontrunner Candidate") -> dict:
    """A genuine 2-tier SCALE-IN cascade (RSI(QQQ) gt 80 -> [nested RSI(QQQ)
    gt 83 -> UVXY]) — real nested structure the detector resolves as ONE
    cascade spanning both tiers (frontrunner_detector.py's own documented
    "scale-in tiers ... reported as ONE cascade" contract). The outer else
    branch is padded with several placeholder holdings so the fire branch
    (containing the whole nested tier) is still the smaller side by node
    count — mirroring the real fire-branch-small-vs-core-branch-large size
    cliff the detector's heuristic keys on. Verified detectable pre-write
    (1 cascade, 2 nested IF levels on render) via a direct interpreter probe."""
    inner_if = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "QQQ", window=10),
            "gt",
            83,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("UVXY")])],
        else_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXM")])],
    )
    outer_if = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "QQQ", window=10),
            "gt",
            80,
        ),
        then_children=[inner_if],
        else_children=[
            symphony_schema.make_weight_equal(
                [symphony_schema.make_asset(f"CORE_ASSET_{i:04d}") for i in range(20)]
            )
        ],
    )
    return symphony_schema.make_root(name, "daily", [outer_if])


def _atlas_result(*candidates: dict, available: bool = True) -> dict:
    """Build a community_strats.load_community_strategies-shaped return dict
    (same real candidate-doc shape as test_frontrunner_atlas_patterns.py's
    own helper: sid/name/tree/tickers/oos_metrics/composition_hash)."""
    docs = []
    for i, tree in enumerate(candidates):
        docs.append(
            {
                "sid": f"atlas-candidate-{i}",
                "name": tree.get("name", f"candidate-{i}"),
                "tree": tree,
                "tickers": [],
                "oos_metrics": {"sharpe": 99.0},
                "composition_hash": f"hash-{i}",
            }
        )
    return {
        "available": available,
        "candidates": docs,
        "stats": {},
        "source": "captplanet",
    }


def _find_json_objects(text: str) -> list:
    """Yield every top-level balanced-brace JSON object that parses from text.
    Mirrors test_build_plan_generator_prompt.py's own scanner exactly, so
    prompt-embedded-example extraction is consistent across the advisors
    test suite."""
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


def _extract_example_overlay_nodes(prompt: str) -> list[dict]:
    """Return every embedded JSON object in the prompt that looks like a
    build-plan-DSL overlay NODE (bare kind='if'/'if_compound', matching how
    frontrunner_builder embeds _EXAMPLE_OVERLAY today — never wrapped in a
    full plan envelope)."""
    out = []
    for obj in _find_json_objects(prompt):
        if isinstance(obj, dict) and obj.get("kind") in ("if", "if_compound"):
            out.append(obj)
    return out


# Structural signal tokens — grounded, not guessed: symphony_schema's own
# render_rules_text emits exactly "IF" / "WHEN" lines for a nested if
# structure (proved directly against the two-tier fixture below in
# test_atlas_pattern_for_tiered_cascade_carries_real_nesting_signal). The
# current 4-scalar fingerprint {vix_tickers, rsi_thresholds, watched_tickers,
# basket_node_count} can never coincidentally contain either token (verified:
# no VIX-family ticker or float threshold spells "IF"/"WHEN").
_STRUCTURAL_TOKENS = ("IF", "WHEN")


# ===========================================================================
# GROUP 1 — AC-1: the prompt invites sophistication, not just an RSI cascade.
# ===========================================================================


def test_prompt_names_at_least_one_indicator_beyond_rsi(fbld):
    """AC-1a: a genuinely multi-signal invitation must name at least one real
    DSL indicator function OTHER than RSI (imported live from
    symphony_schema.KNOWN_INDICATOR_FNS, never hardcoded) — not just describe
    a single-signal 'RSI-overbought cascade'. RED today: the prompt names
    ONLY 'relative-strength-index' and zero other known indicator fns."""
    prompt = fbld._build_generation_prompt({"watched_tickers": ["SPY"]})
    non_rsi_fns = symphony_schema.KNOWN_INDICATOR_FNS - {"relative-strength-index"}
    named = [fn for fn in sorted(non_rsi_fns) if fn in prompt]
    assert named, (
        f"prompt names zero indicator functions beyond RSI — it is scoped to "
        f"a single-signal RSI-overbought cascade only, never inviting a "
        f"second/different signal type. Known non-RSI fns checked: "
        f"{sorted(non_rsi_fns)}"
    )


_COMPOUND_INVITATION_MARKERS = (
    "combine multiple",
    "combine two",
    "combine indicators",
    "combine signals",
    "may combine",
    "can combine",
    "consider a compound",
    "consider combining",
    "consider a multi-signal",
    "use a compound condition when",
    "does not need to be a single",
    "need not be a single",
    "not limited to a single",
)


def test_prompt_proactively_invites_compound_design_not_just_mechanical_collapse(fbld):
    """AC-1a: the prompt's ONLY current mention of compound-adjacent logic is
    a passive, mechanical note ('a downstream step collapses mergeable
    rungs') plus a syntax description of if_compound as a bare grammar
    option — never a proactive invitation to COMPOSE a compound/multi-signal
    condition when it captures the pattern better than a flat cascade. RED
    today: none of a reasonable set of invitation phrasings appears anywhere
    in the prompt."""
    prompt = fbld._build_generation_prompt({"watched_tickers": ["SPY"]}).lower()
    assert any(marker in prompt for marker in _COMPOUND_INVITATION_MARKERS), (
        f"prompt contains no proactive invitation to design a compound/"
        f"multi-signal condition — expected at least one phrasing from "
        f"{_COMPOUND_INVITATION_MARKERS!r}"
    )


def test_prompt_embeds_a_compound_if_compound_exemplar_that_compiles_clean(fbld):
    """AC-1b: the prompt must embed >=1 COMPILER-VERIFIED compound (if_compound
    with a genuine binary_compound/compound condition-type union) worked
    exemplar — not only the existing flat 2-tier kind='if' _EXAMPLE_OVERLAY.
    ADVERSARIAL CORE: a degenerate if_compound whose condition type is the
    single-ticker 'binary' union (structurally no richer than a flat if) does
    NOT satisfy this — the exemplar must use binary_compound or compound,
    proving genuine multi-signal/multi-ticker sophistication. RED today: the
    ONLY embedded example is _EXAMPLE_OVERLAY, kind='if', condition has no
    'type' union at all."""
    prompt = fbld._build_generation_prompt({"watched_tickers": ["SPY"]})
    overlay_examples = _extract_example_overlay_nodes(prompt)
    compound_examples = [
        n
        for n in overlay_examples
        if n.get("kind") == "if_compound"
        and isinstance(n.get("condition"), dict)
        and n["condition"].get("type") in ("binary_compound", "compound")
    ]
    assert compound_examples, (
        f"no genuinely compound (if_compound, condition type binary_compound/"
        f"compound) worked exemplar is embedded in the generation prompt — "
        f"found overlay example kinds/condition-types="
        f"{[(n.get('kind'), (n.get('condition') or {}).get('type')) for n in overlay_examples]!r}"
    )

    found_compiling = False
    reasons = []
    for node in compound_examples:
        plan_envelope = {
            "plan_id": "frontrunner-exemplar-check",
            "objective": "cut_drawdown",
            "name": "Frontrunner Exemplar Check",
            "rebalance": "daily",
            "root": node,
        }
        result = plan_tree_compiler.compile_plan(plan_envelope)
        if result.tree is not None and symphony_schema.validate_tree(result.tree) == []:
            found_compiling = True
            break
        reasons.append(result.reason)
    assert found_compiling, (
        f"the embedded compound if_compound exemplar(s) do not compile clean "
        f"through the real plan_tree_compiler (reasons={reasons!r}) — a "
        f"non-compiling exemplar would teach Fable an invalid shape"
    )


# ===========================================================================
# GROUP 2 — AC-4/AC-5 (frontrunner half): Atlas fed as real structure.
# ===========================================================================


def test_atlas_pattern_for_tiered_cascade_carries_real_nesting_signal(fbld):
    """AC-4: a genuinely 2-tier scale-in Atlas candidate must contribute a
    pattern carrying real STRUCTURAL signal (nesting/tier tokens) — not just
    the flattened 4-scalar fingerprint {vix_tickers, rsi_thresholds,
    watched_tickers, basket_node_count}, which aggregates VIX tickers and
    thresholds across every tier but discards WHICH ticker fires at WHICH
    tier and the nesting relationship entirely (indistinguishable from N
    unrelated single-tier cascades merged). RED today: probe-confirmed the
    fed payload has zero IF/WHEN structural tokens no matter how tiered the
    source cascade is."""
    from advisors import frontrunner_detector

    tree = _make_two_tier_frontrunner_shaped_tree()

    # Fixture sanity — independently confirm this tree really is ONE
    # 2-tier cascade with real nesting, via the real detector + the real
    # renderer (the source of truth for what "structural token" means here).
    detection = frontrunner_detector.detect_frontrunner_cascades(tree)
    assert len(detection.cascades) == 1, (
        f"fixture setup: expected exactly one detected cascade, got "
        f"{len(detection.cascades)} (skip_reason={detection.skip_reason!r})"
    )
    rendered = symphony_schema.render_rules_text(detection.cascades[0].overlay_tree)
    assert rendered.count("IF") >= 2, (
        f"fixture setup: expected a genuinely 2-tier (>=2 nested IF) cascade, "
        f"rendered={rendered!r}"
    )

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(tree),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert patterns, "fixture must produce at least one pattern for this assertion to be meaningful"
    serialized = json.dumps(patterns)
    assert any(tok in serialized for tok in _STRUCTURAL_TOKENS), (
        f"extracted Atlas pattern(s) for a genuine 2-tier scale-in cascade "
        f"carry no nesting/tier structural signal (checked for "
        f"{_STRUCTURAL_TOKENS!r}) — patterns={patterns!r}"
    )


def test_atlas_patterns_collapse_near_identical_candidates_not_return_five_raw_copies(fbld):
    """AC-4 de-dup: 5 structurally-identical frontrunner-shaped Atlas
    candidates (same VIX ticker, same RSI threshold, same watched ticker —
    only the wrapper symphony name differs) must not surface as 5 separate
    raw pattern entries — real structural exemplars should collapse
    near-duplicates, not append one entry per candidate regardless of
    duplication. RED today: _gather_atlas_frontrunner_patterns appends
    exactly one pattern dict per detected cascade unconditionally — 5
    identical-shaped candidates always yield len(patterns) == 5."""
    trees = [_make_frontrunner_shaped_tree(f"Atlas Clone {i}") for i in range(5)]

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(*trees),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert len(patterns) < 5, (
        f"5 structurally-identical Atlas candidates yielded {len(patterns)} "
        f"raw pattern entries — AC-4 requires collapsing near-identical "
        f"exemplars, not returning one entry per candidate regardless of "
        f"duplication: patterns={patterns!r}"
    )


def test_prompt_reflects_atlas_structural_content_when_present_vs_absent(fbld):
    """AC-5 (frontrunner half) — 'named input actually varies the result',
    combined with AC-4's structure requirement: a genuinely tiered Atlas
    corpus must produce a prompt that DIFFERS STRUCTURALLY (carries an
    IF/WHEN nesting token) from the same prompt with no Atlas patterns
    supplied — not merely differ in some incidental scalar/ticker text. RED
    today: the fed atlas_patterns carry zero structural tokens regardless of
    whether the source corpus is tiered or absent, so swapping the corpus
    in/out never changes the prompt's structural-token content."""
    from advisors import frontrunner_detector

    tree = _make_two_tier_frontrunner_shaped_tree()
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(tree),
    ):
        patterns_present = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])
    patterns_absent: list = []

    # Fixture sanity: reuse the same real-detector proof as the previous test
    # (the corpus really is tiered) so a failure here is attributable to the
    # prompt boundary, not to the extraction step already covered above.
    detection = frontrunner_detector.detect_frontrunner_cascades(tree)
    assert len(detection.cascades) == 1

    prompt_present = fbld._build_generation_prompt(
        {"watched_tickers": ["QQQ"], "atlas_patterns": patterns_present}
    )
    prompt_absent = fbld._build_generation_prompt(
        {"watched_tickers": ["QQQ"], "atlas_patterns": patterns_absent}
    )

    assert prompt_present != prompt_absent, (
        "swapping a genuinely tiered Atlas corpus in vs. out produced an "
        "IDENTICAL generation prompt — the Atlas input is not reaching the "
        "prompt at all"
    )
    structural_hits = [
        tok for tok in _STRUCTURAL_TOKENS if tok in prompt_present and tok not in prompt_absent
    ]
    assert structural_hits, (
        f"the tiered Atlas corpus produced a different prompt, but none of "
        f"that difference is STRUCTURAL (checked for {_STRUCTURAL_TOKENS!r}) "
        f"— the prompt varies only in scalar/incidental content, never "
        f"conveying real nesting/tier structure"
    )


# ===========================================================================
# GROUP 3 — AC-8: per-symphony specificity (the step/kind guard bug).
# ===========================================================================


def _run_build_capturing_signal_context(fbld_module, incumbent_symphony: dict, symphony_id: str) -> dict | None:
    """Run _run_build_for_symphony against a mocked incumbent, capturing the
    signal_context passed to generate_candidate_overlay on its first call.
    Mirrors test_frontrunner_atlas_patterns.py's own established mocking
    idiom exactly (patch symphony_logic.fetch_symphony_score + Atlas loader +
    generate_candidate_overlay + the database write seams)."""
    captured: list[dict] = []

    def _capture_and_stop(signal_context, **kwargs):
        captured.append(signal_context)
        return fbld_module.GenerationResult(candidate=None, error="stop-after-capture")

    with (
        patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
        patch(
            "advisors.community_strats.load_community_strategies",
            return_value={"available": False, "candidates": [], "stats": {}, "source": "captplanet"},
        ),
        patch.object(fbld_module, "generate_candidate_overlay", side_effect=_capture_and_stop),
        patch("database.insert_frontrunner_proposal"),
        patch("database.insert_advisor_observation"),
        patch("database.insert_dof_ledger_row"),
    ):
        fbld_module._run_build_for_symphony(symphony_id)

    return captured[0] if captured else None


def test_run_build_for_symphony_populates_watched_tickers_from_step_keyed_overlay(fbld):
    """AC-8: _run_build_for_symphony's watched_tickers computation
    (`_walk_overlay_tickers(cascade.overlay_tree) if "kind" in cascade.overlay_tree
    else []`) evaluates the else-branch on EVERY real Composer overlay_tree,
    because frontrunner_detector's overlay_tree is step-keyed ('step',
    'children'), never kind-keyed ('kind') — the guard condition is
    permanently False. RED today: watched_tickers is always [] for a real
    detected cascade that plainly references SPY."""
    # Built directly (not via the shared _make_frontrunner_shaped_tree helper,
    # which defaults to QQQ) so this test's own SPY-specific assertions are
    # self-explanatory.
    incumbent_symphony = symphony_schema.make_root(
        "Watched-Ticker Test Symphony",
        "daily",
        [
            symphony_schema.make_if(
                symphony_schema.make_condition(
                    symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
                    "gt",
                    80,
                ),
                then_children=[
                    symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])
                ],
                else_children=[
                    symphony_schema.make_weight_equal(
                        [symphony_schema.make_asset("CORE_ASSET_0001")]
                    )
                ],
            )
        ],
    )

    signal_context = _run_build_capturing_signal_context(
        fbld, incumbent_symphony, "watched-ticker-test-symphony"
    )
    assert signal_context is not None, "generate_candidate_overlay was never reached"

    watched = signal_context.get("watched_tickers")
    assert watched, (
        f"watched_tickers is empty/missing for a real detected cascade whose "
        f"condition plainly watches SPY — the step/kind guard bug leaves "
        f"this always [] (signal_context={signal_context!r})"
    )
    assert "SPY" in watched, f"expected SPY in watched_tickers, got {watched!r}"


def test_two_distinct_incumbent_symphonies_yield_distinct_watched_tickers_and_prompts(fbld):
    """AC-8: two symphonies watching genuinely different core tickers must
    produce DIFFERENT watched_tickers (and therefore different prompts) — not
    the identical '(none supplied)' prompt every symphony gets today because
    the guard bug zeroes watched_tickers unconditionally regardless of the
    incumbent's real content."""

    def _make_incumbent(ticker: str) -> dict:
        cascade = symphony_schema.make_if(
            symphony_schema.make_condition(
                symphony_schema.make_indicator("relative-strength-index", ticker, window=10),
                "gt",
                80,
            ),
            then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
            else_children=[
                symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
            ],
        )
        return symphony_schema.make_root(f"Symphony-{ticker}", "daily", [cascade])

    ctx_spy = _run_build_capturing_signal_context(fbld, _make_incumbent("SPY"), "sym-spy")
    ctx_qqq = _run_build_capturing_signal_context(fbld, _make_incumbent("QQQ"), "sym-qqq")

    assert ctx_spy is not None and ctx_qqq is not None, (
        "generate_candidate_overlay was not reached for at least one incumbent"
    )
    assert ctx_spy.get("watched_tickers") != ctx_qqq.get("watched_tickers"), (
        f"two symphonies watching different core tickers produced identical "
        f"watched_tickers — SPY-symphony={ctx_spy.get('watched_tickers')!r} "
        f"QQQ-symphony={ctx_qqq.get('watched_tickers')!r}"
    )

    prompt_spy = fbld._build_generation_prompt(ctx_spy)
    prompt_qqq = fbld._build_generation_prompt(ctx_qqq)
    assert prompt_spy != prompt_qqq, (
        "two symphonies with distinct watched tickers must not receive the "
        "identical generation prompt"
    )


def test_gather_atlas_frontrunner_patterns_watched_tickers_populated_from_step_keyed_overlay(fbld):
    """AC-8 (Atlas side of the SAME guard bug): _gather_atlas_frontrunner_patterns'
    own watched_tickers extraction
    (`_walk_overlay_tickers(cascade.overlay_tree) if "kind" in cascade.overlay_tree
    else []`, ~frontrunner_builder.py:1183) has the identical step/kind guard
    bug as the _run_build_for_symphony call site — cascade.overlay_tree is
    always step-keyed, so every extracted Atlas pattern's own watched_tickers
    field is unconditionally []. RED today."""
    tree = _make_frontrunner_shaped_tree()  # RSI(QQQ) gt 81 -> UVXY fire

    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_atlas_result(tree),
    ):
        patterns = fbld._gather_atlas_frontrunner_patterns(watched_tickers=["QQQ"])

    assert patterns, "fixture must produce at least one pattern for this assertion to be meaningful"
    found_watched = any(p.get("watched_tickers") for p in patterns if isinstance(p, dict))
    assert found_watched, (
        f"every extracted Atlas pattern has an empty watched_tickers field "
        f"even though the source cascade plainly watches QQQ — the "
        f"step/kind guard bug zeroes this unconditionally: patterns={patterns!r}"
    )


# ===========================================================================
# Regression guard: static-boilerplate hygiene for the structural tokens
# used above (belt-and-suspenders — proves the RED tests aren't accidentally
# trivially satisfied by prompt boilerplate that already exists today).
# ===========================================================================


def test_structural_tokens_are_not_already_present_in_the_bare_prompt_boilerplate(fbld):
    """Sanity guard for this file's own RED tests: confirms the case-sensitive
    'IF'/'WHEN' structural tokens genuinely do not appear anywhere in the
    prompt's static boilerplate (no watched_tickers, no atlas_patterns) —
    if they did, the structural-token assertions above would be meaningless
    (always trivially true)."""
    prompt = fbld._build_generation_prompt({})
    for tok in _STRUCTURAL_TOKENS:
        assert tok not in prompt, (
            f"structural token {tok!r} already present in bare prompt "
            f"boilerplate — the structural-token RED tests in this file "
            f"would not be meaningful"
        )
