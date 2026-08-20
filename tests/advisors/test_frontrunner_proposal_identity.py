"""RED tests — feature-plans/frontrunner-proposal-identity.md (AC-1/2/3/6/7-approve/8).

Module under test: advisors.frontrunner_builder (EXISTING module). This file
covers the three NEW pure helpers, the persist-site stamping inside
_run_build_for_symphony, and the approve-path name/description wiring.
Route/template coverage (AC-4/5/6-render/7-render/9) lives in
tests/app/test_frontrunner_proposal_identity_template.py.

WHY THIS FEATURE EXISTS (operator ruling, 2026-08-20): a frontrunner
proposal's candidate_tree is a FULL standalone copy of the incumbent symphony
with one cascade replaced by a generated overlay — but every surface named it
as if it were a bare frontrunner ("Frontrunner Candidate — <hash>", a generic
description). This file locks the honest replacement contract.

CRITICAL DESIGN NOTE — what "overlay_tree" IS (verified directly against
advisors/frontrunner_builder.py before writing this file, and against
_EXAMPLE_OVERLAY/_EXAMPLE_COMPOUND_OVERLAY's real shape):

  splice_candidate_into_symphony(incumbent_symphony, cascade, candidate)
  COMPILES `candidate` (a raw build-plan-DSL node — has a "kind" key, e.g.
  "kind": "if" — see _EXAMPLE_OVERLAY) into a Composer-shaped node, then
  GRAFTS the incumbent's real else-branch content into that compiled node's
  terminal else slot (RC#3, _graft_incumbent_core) BEFORE splicing it into
  the full tree. The POST-GRAFT node therefore contains the ENTIRE incumbent
  core in its else branch — persisting THAT into metrics_json["overlay_tree"]
  would duplicate the whole ~186KB symphony into every proposal row and
  recreate the exact "shows the whole tree again" confusion this feature
  exists to kill (team-lead ruling, plan-approval MOD 1).

  The correct object to persist is `result.candidate` ITSELF — the raw DSL
  node exactly as generate_candidate_overlay returned it and exactly as
  passed INTO splice_candidate_into_symphony's `candidate` argument. It is
  small (one condition + a two-branch fire/placeholder pair) and its else
  branch is the LLM's own CORE_STRATEGY_PLACEHOLDER-style asset node — a
  self-explanatory stand-in for "your whole existing strategy goes here",
  not the strategy itself. splice_candidate_into_symphony's own signature
  and return value (dict | None, the full spliced tree) are UNCHANGED by
  this feature — two existing GREEN tests
  (test_splice_into_incumbent_yields_a_validate_tree_clean_symphony,
  test_splice_preserves_the_core_content_past_the_boundary) treat that
  return value as the bare tree dict and must stay green.

  `replaced_node_id` is independently derivable at the SAME call site from
  data already in scope — `cascade.overlay_tree.get("id")` — with zero
  change to splice_candidate_into_symphony either.

HANDOFF CONTRACT (Section A, fpi-impl-builder — advisors/frontrunner_builder.py only):

  1. build_proposal_symphony_name(display_name: str, proposal_id: int, source: str) -> str
     - source == "frontrunner_builder":
         f"{display_name} + FR overlay (full copy, #{proposal_id})"
     - source == "strategy_builder_retrofit":
         f"Strategy Builder candidate for {display_name} (from scratch, #{proposal_id})"
     - Bounded by a new module constant MAX_PROPOSAL_NAME_CHARS = 100. The
       "#{proposal_id})" token (with its closing paren) must always survive
       intact at the end of the string; display_name is truncated with a
       trailing "…" ellipsis to make room when the full formatted string
       would exceed the bound.
     - Hash-fallback is the CALLER's job (approve_frontrunner_proposal passes
       the raw incumbent hash string AS display_name when
       database.load_state() resolution misses) — this helper just formats
       whatever string it is given. Keeps it pure/trivially testable.

  2. build_proposal_symphony_description(*, display_name, incumbent_hash,
     proposal_id, created_at, source, replaced_node_id=None,
     overlay_summary=None) -> str
     - Bounded by a new module constant MAX_PROPOSAL_DESCRIPTION_CHARS = 1000.
     - ALWAYS includes: display_name, incumbent_hash, str(proposal_id),
       created_at, and the sentence "Undeployed candidate — review before
       investing." verbatim.
     - source == "frontrunner_builder": additionally includes
       replaced_node_id and overlay_summary when both are non-None, plus the
       sentence "Full standalone copy — the original symphony is not
       modified." verbatim. When either replaced_node_id or overlay_summary
       is None (AC-6 legacy row), degrades honestly — includes the fallback
       phrase "overlay not recorded for this proposal" instead of
       fabricating/omitting silently, and the literal string "None" must
       never appear anywhere in the output.
     - source == "strategy_builder_retrofit": NEVER includes the "Full
       standalone copy" sentence (that claim would be false for a
       from-scratch candidate) — instead includes wording naming it a
       from-scratch candidate that does not contain the incumbent's logic
       (team-lead-approved honesty extension, MOD 3, mirrors the AC-7 card
       wording below).

  3. summarize_overlay(overlay_tree: dict | None) -> str
     - Operates on the RAW DSL node shape (kind="if"/"if_compound", a
       "condition" dict, "then"/"else" lists of DSL nodes) — the SAME shape
       _dsl_flat_if_overlay/_EXAMPLE_OVERLAY produce, NOT the compiled
       Composer step-shape.
     - Flat condition (overlay_tree["condition"] has an "lhs_fn" key): derive
       a SPECIFIC one-liner that names both the signal ticker
       (condition["lhs_ticker"]) and the fire-branch ticker (walk "then" for
       the first {"kind":"asset","ticker":...} leaf, however nested inside
       weight/group wrappers).
     - Compound condition, missing/malformed "condition", or any other
       shape that doesn't yield a derivable flat signal: degrade to the
       EXACT literal "compound condition overlay". Never raises for any
       input (None, {}, non-dict, partial dict).

  4. Persist site (_run_build_for_symphony, immediately after the existing
     metrics["signal_provenance"] = ... assignment, before
     database.insert_frontrunner_proposal(...)):
         metrics["overlay_tree"] = result.candidate
         metrics["replaced_node_id"] = (
             cascade.overlay_tree.get("id")
             if isinstance(cascade.overlay_tree, dict) else None
         )
         metrics["overlay_summary"] = summarize_overlay(result.candidate)
     `cascade` and `result` are both already in scope at this point in the
     existing loop body — zero new plumbing, zero change to
     splice_candidate_into_symphony's signature.

  5. approve_frontrunner_proposal (:1931-1939 region): replace the hardcoded
     name=f"Frontrunner Candidate — {proposal['symphony_id']}" /
     description="Frontrunner Builder candidate (operator-approved)" with:
       - resolve display_name by scanning database.load_state() for a
         sym_dict whose key == proposal["symphony_id"] (mirrors the
         resolved_hash pattern at app.py:7190-7216 — NAME->hash is the
         opposite direction there; here it's hash-key->sym_dict["name"]),
         falling back to proposal["symphony_id"] itself when no match.
       - name = build_proposal_symphony_name(display_name, proposal["id"], proposal["proposal_source"])
       - description = build_proposal_symphony_description(
             display_name=display_name, incumbent_hash=proposal["symphony_id"],
             proposal_id=proposal["id"], created_at=proposal["created_at"],
             source=proposal["proposal_source"],
             replaced_node_id=(proposal.get("metrics_json") or {}).get("replaced_node_id"),
             overlay_summary=(proposal.get("metrics_json") or {}).get("overlay_summary"),
         )
     `database` is already lazy-imported inside this function — reuse that
     same reference for load_state(), never a second import.

TEST EXECUTION: python -m pytest tests/advisors/test_frontrunner_proposal_identity.py -n0 -q
(plus the full advisors+app+security touched-suite run before cycle-complete
— never the uncapped full tree).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-under-test import guard.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fbld():
    import advisors.frontrunner_builder as _fbld  # noqa: PLC0415

    return _fbld


# ---------------------------------------------------------------------------
# Group A: build_proposal_symphony_name (AC-1, AC-7-name)
# ---------------------------------------------------------------------------


class TestBuildProposalSymphonyName:
    def test_frontrunner_builder_source_uses_full_copy_overlay_format(self, fbld):
        name = fbld.build_proposal_symphony_name("My Symphony", 5, "frontrunner_builder")
        assert name == "My Symphony + FR overlay (full copy, #5)", (
            f"AC-1 locked format not produced — got {name!r}"
        )

    def test_retrofit_source_uses_strategy_builder_candidate_format(self, fbld):
        name = fbld.build_proposal_symphony_name("My Symphony", 9, "strategy_builder_retrofit")
        assert name == "Strategy Builder candidate for My Symphony (from scratch, #9)", (
            f"AC-7 locked retrofit format (MOD 2) not produced — got {name!r}. The '+' "
            "composition format is reserved for genuine full-copy frontrunner proposals — "
            "a retrofit candidate contains NONE of the incumbent's logic."
        )

    def test_retrofit_name_never_contains_fr_overlay_wording(self, fbld):
        name = fbld.build_proposal_symphony_name("Any Name", 1, "strategy_builder_retrofit")
        assert "FR overlay" not in name and "full copy" not in name, (
            f"retrofit name leaked frontrunner full-copy wording: {name!r}"
        )

    def test_hash_fallback_input_produces_a_sane_name(self, fbld):
        """Hash-fallback resolution is the CALLER's job (approve_frontrunner_proposal) —
        this helper just formats whatever string it's given. Proves the helper
        works correctly when handed a raw Composer hash instead of a display name."""
        name = fbld.build_proposal_symphony_name("8FAXAnQmYi1INDubazeC", 3, "frontrunner_builder")
        assert name == "8FAXAnQmYi1INDubazeC + FR overlay (full copy, #3)"

    def test_two_different_proposal_ids_produce_unique_names(self, fbld):
        name1 = fbld.build_proposal_symphony_name("My Symphony", 101, "frontrunner_builder")
        name2 = fbld.build_proposal_symphony_name("My Symphony", 102, "frontrunner_builder")
        assert name1 != name2, (
            "two proposals from the same run (real droplet data: two rows created two "
            "minutes apart) must get unique names via the #{proposal_id} suffix"
        )
        assert name1.endswith("#101)") and name2.endswith("#102)")

    def test_name_bounded_to_100_chars_with_ellipsis_and_suffix_preserved(self, fbld):
        long_display_name = "X" * 200
        name = fbld.build_proposal_symphony_name(long_display_name, 123456, "frontrunner_builder")
        assert len(name) <= 100, f"name exceeded the 100-char bound: {len(name)} chars"
        assert name.endswith("#123456)"), (
            f"the #{{proposal_id}}) suffix must survive truncation intact — got {name!r}"
        )
        assert "…" in name, (
            f"a truncated display_name must carry an ellipsis marker, not silently "
            f"chop the string — got {name!r}"
        )
        assert long_display_name not in name, (
            "the full 200-char display_name must NOT appear verbatim — truncation did "
            "not actually occur"
        )

    def test_retrofit_name_also_bounded_and_suffix_preserved(self, fbld):
        """The retrofit format embeds display_name in the MIDDLE of the string
        (not just appended at the end) — the truncation contract must still hold."""
        long_display_name = "Y" * 200
        name = fbld.build_proposal_symphony_name(
            long_display_name, 42, "strategy_builder_retrofit"
        )
        assert len(name) <= 100
        assert name.endswith("#42)")
        assert long_display_name not in name

    def test_empty_display_name_omits_blank_identity_slot_frontrunner_source(self, fbld):
        """F2 (Revise 2): a weekly-scheduler retrofit row can carry
        symphony_id='' (unresolvable), which flows through as an empty-string
        display_name. The frontrunner_builder format ("{name} + FR overlay...")
        must NEVER render as " + FR overlay (full copy, #N)" (leading space,
        blank identity slot) — the "{name} + " segment must be omitted
        entirely when display_name is empty."""
        name = fbld.build_proposal_symphony_name("", 5, "frontrunner_builder")
        assert name == "FR overlay (full copy, #5)", f"got {name!r}"
        assert not name.startswith(" "), f"leading space from a blank identity slot: {name!r}"

    def test_empty_display_name_omits_blank_identity_slot_retrofit_source(self, fbld):
        """F2 (Revise 2): the retrofit format ('Strategy Builder candidate for
        {name} ...') must NEVER render 'Strategy Builder candidate for  (from
        scratch, #N)' (double space, blank identity slot) — the ' for {name}'
        clause must be omitted entirely when display_name is empty."""
        name = fbld.build_proposal_symphony_name("", 5, "strategy_builder_retrofit")
        assert name == "Strategy Builder candidate (from scratch, #5)", f"got {name!r}"
        assert "  " not in name, f"double space from a blank identity slot: {name!r}"


# ---------------------------------------------------------------------------
# Group B: build_proposal_symphony_description (AC-2, AC-6, AC-7-description)
# ---------------------------------------------------------------------------


class TestBuildProposalSymphonyDescription:
    def test_frontrunner_source_contains_all_provenance_fields(self, fbld):
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="8FAXAnQmYi1INDubazeC",
            proposal_id=17,
            created_at="2026-08-20T12:00:00Z",
            source="frontrunner_builder",
            replaced_node_id="cascade-node-id-abc",
            overlay_summary="RSI(SPY,10) > 80 hedges into UVXY",
        )
        for expected in (
            "My Symphony",
            "8FAXAnQmYi1INDubazeC",
            "17",
            "2026-08-20T12:00:00Z",
            "cascade-node-id-abc",
            "RSI(SPY,10) > 80 hedges into UVXY",
        ):
            assert expected in description, (
                f"description is missing provenance field {expected!r} — full text: "
                f"{description!r}"
            )

    def test_frontrunner_source_contains_locked_full_copy_sentence(self, fbld):
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="frontrunner_builder",
            replaced_node_id="node-1",
            overlay_summary="summary",
        )
        assert "Full standalone copy — the original symphony is not modified." in description

    @pytest.mark.parametrize("source", ["frontrunner_builder", "strategy_builder_retrofit"])
    def test_both_sources_contain_locked_undeployed_review_sentence(self, fbld, source):
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source=source,
        )
        assert "Undeployed candidate — review before investing." in description, (
            f"source={source!r} description is missing the universal review-before-"
            f"investing sentence — full text: {description!r}"
        )

    def test_ac6_legacy_row_degrades_honestly_without_crash_or_none_leak(self, fbld):
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="frontrunner_builder",
            replaced_node_id=None,
            overlay_summary=None,
        )
        assert fbld.OVERLAY_NOT_RECORDED_TEXT in description, (
            f"AC-6 legacy-row degrade must render the SHARED fallback constant "
            f"(fbld.OVERLAY_NOT_RECORDED_TEXT), not silently omit/fabricate, and not a "
            f"locally-duplicated copy that can drift from the template's own copy of the "
            f"same text (F10) — full text: {description!r}"
        )
        assert "None" not in description, (
            f"a bare Python 'None' leaked into the description via naive f-string "
            f"formatting — full text: {description!r}"
        )

    def test_ac6_legacy_row_still_states_full_standalone_copy(self, fbld):
        """Regression lock (sufficiency-review finding, fpi-test-writer):
        implementation correctly makes the "Full standalone copy" sentence
        UNCONDITIONAL for source="frontrunner_builder", regardless of whether
        the AC-3 overlay-identity metrics (replaced_node_id/overlay_summary)
        happen to be recorded — the full-copy property belongs to the SPLICE
        itself, not to whether those two metrics fields were captured. A
        legacy row predating AC-3 is STILL a genuine full spliced copy; only
        the specific replaced-node/overlay-summary DETAIL is what's honestly
        unrecorded. Locks this correct, more-honest-than-originally-specified
        behavior against a future "fix" that wrongly makes it conditional."""
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="frontrunner_builder",
            replaced_node_id=None,
            overlay_summary=None,
        )
        assert "Full standalone copy — the original symphony is not modified." in description, (
            f"a legacy frontrunner_builder-source row (no AC-3 overlay metrics) is "
            f"STILL a genuine full spliced copy — the full-copy sentence must remain "
            f"present even when replaced_node_id/overlay_summary are None. Full text: "
            f"{description!r}"
        )

    def test_description_bounded_by_max_chars_under_pathological_overlay_summary(self, fbld):
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="frontrunner_builder",
            replaced_node_id="node-1",
            overlay_summary="Z" * 5000,
        )
        assert len(description) <= 1000, (
            f"description exceeded the 1000-char Security Consideration bound: "
            f"{len(description)} chars"
        )

    def test_retrofit_source_states_from_scratch_and_omits_full_copy_claim(self, fbld):
        """MOD 3 (team-lead-approved honesty extension): a retrofit candidate
        contains NONE of the incumbent's logic — claiming 'Full standalone copy'
        in its Composer description would recreate the exact misleading-identity
        defect this feature exists to fix."""
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="strategy_builder_retrofit",
        )
        assert "from scratch" in description.lower() or "from-scratch" in description.lower(), (
            f"retrofit description must name itself as a from-scratch candidate — full "
            f"text: {description!r}"
        )
        assert "does not contain the incumbent" in description.lower(), (
            f"retrofit description must state it does not contain the incumbent's logic "
            f"— full text: {description!r}"
        )
        assert "Full standalone copy — the original symphony is not modified." not in description, (
            f"retrofit description falsely claimed to be a full standalone copy — full "
            f"text: {description!r}"
        )
        assert "Undeployed candidate — review before investing." in description

    def test_retrofit_first_sentence_never_says_frontrunner_builder_or_incumbent(self, fbld):
        """F4 (Revise 2): the description's FIRST sentence was previously
        UNCONDITIONAL — "Frontrunner Builder candidate for X (incumbent
        <hash>)..." — even for source="strategy_builder_retrofit", directly
        contradicting the from-scratch/does-not-contain-the-incumbent's-logic
        sentence appended right after it. The first sentence itself must be
        source-branched: never claim "Frontrunner Builder candidate" and
        never claim an "incumbent" relationship for a retrofit row."""
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="strategy_builder_retrofit",
        )
        assert "Frontrunner Builder candidate" not in description, (
            f"retrofit description's first sentence still claims 'Frontrunner Builder "
            f"candidate' — full text: {description!r}"
        )
        assert "incumbent" not in description.lower(), (
            f"retrofit description must never reference an 'incumbent' relationship — "
            f"a from-scratch candidate has none — full text: {description!r}"
        )
        # Provenance fields the retrofit description SHOULD still carry.
        for expected in ("My Symphony", "1", "2026-08-20T00:00:00Z"):
            assert expected in description, (
                f"retrofit description is missing provenance field {expected!r} even "
                f"after removing the frontrunner-specific first sentence — full text: "
                f"{description!r}"
            )

    def test_frontrunner_first_sentence_still_says_frontrunner_builder_candidate(self, fbld):
        """The inverse of the above — confirms the F4 fix source-BRANCHES rather
        than simply deleting the frontrunner-specific first sentence for both
        sources."""
        description = fbld.build_proposal_symphony_description(
            display_name="My Symphony",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="frontrunner_builder",
            replaced_node_id="node-1",
            overlay_summary="summary",
        )
        assert "Frontrunner Builder candidate" in description
        assert "incumbent" in description.lower()

    def test_empty_display_name_description_omits_blank_slot_frontrunner_source(self, fbld):
        """F2 (Revise 2): an empty-string display_name (weekly-scheduler retrofit
        row's symphony_id='' flowing through) must never render 'candidate for
        (incumbent hash-1)' (blank slot before the parenthetical) for the
        frontrunner_builder source either."""
        description = fbld.build_proposal_symphony_description(
            display_name="",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="frontrunner_builder",
            replaced_node_id="node-1",
            overlay_summary="summary",
        )
        assert "for  (" not in description and "for (" not in description, (
            f"a blank identity slot leaked into the description — full text: "
            f"{description!r}"
        )
        assert "hash-1" in description

    def test_empty_display_name_description_omits_blank_slot_retrofit_source(self, fbld):
        """F2 (Revise 2): same, for the retrofit source's own first-sentence
        wording (post-F4-fix, whatever that ends up being) — no blank
        identity slot regardless of the exact template chosen."""
        description = fbld.build_proposal_symphony_description(
            display_name="",
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="strategy_builder_retrofit",
        )
        assert "for  " not in description, (
            f"a blank identity slot (double space after 'for') leaked into the "
            f"retrofit description — full text: {description!r}"
        )
        assert "1" in description  # proposal_id still present

    def test_description_bounded_and_safety_sentence_survives_truncation(self, fbld):
        """F7 (Revise 2): the previous implementation (a) left display_name
        completely UNBOUNDED in the description path (unlike the name
        builder, which bounds it), and (b) truncated the ASSEMBLED string
        from the tail — which can chop off the universal "Undeployed
        candidate — review before investing." safety sentence (appended
        LAST) when a long display_name pushes the total past the 1000-char
        bound. Both must be fixed: the description stays <= 1000 chars AND
        the safety sentence survives intact at the end regardless of how
        long display_name is."""
        description = fbld.build_proposal_symphony_description(
            display_name="X" * 5000,
            incumbent_hash="hash-1",
            proposal_id=1,
            created_at="2026-08-20T00:00:00Z",
            source="frontrunner_builder",
            replaced_node_id="node-1",
            overlay_summary="summary",
        )
        assert len(description) <= 1000, (
            f"description with a pathological 5000-char display_name exceeded the "
            f"1000-char bound: {len(description)} chars"
        )
        assert description.endswith("Undeployed candidate — review before investing."), (
            f"the universal safety sentence must survive ANY truncation — got a "
            f"description ending: {description[-120:]!r}"
        )


class TestResolveIncumbentDisplayName:
    """F8 (Revise 2): the display-name resolution logic (hash->name lookup
    against bot_state, honest hash fallback) was independently duplicated
    between approve_frontrunner_proposal (advisors/frontrunner_builder.py)
    and app.py's ai_advisor_tab() prefetch loop — an honesty-drift risk
    between the Composer upload name and the dashboard card identity line.
    Extracted here as ONE shared, pure, unit-testable function:
    resolve_incumbent_display_name(bot_state: dict, symphony_id: str) -> str.
    Both call sites must route through it (app.py via an import — CC-2 lazy
    or top-level, implementer's call; the existing approve-flow and
    route/template tests already prove BOTH call sites still resolve names
    correctly post-refactor, so this class covers the pure function directly
    rather than re-proving call-site behavior already covered elsewhere)."""

    def test_resolves_display_name_when_present_in_bot_state(self, fbld):
        bot_state = {"hash-abc": {"name": "My Real Symphony"}}
        result = fbld.resolve_incumbent_display_name(bot_state, "hash-abc")
        assert result == "My Real Symphony"

    def test_falls_back_to_the_raw_hash_when_unresolvable(self, fbld):
        result = fbld.resolve_incumbent_display_name({}, "unresolvable-hash-999")
        assert result == "unresolvable-hash-999"

    def test_falls_back_to_the_raw_hash_when_bot_state_entry_is_not_a_dict(self, fbld):
        bot_state = {"hash-weird": "not-a-dict"}
        result = fbld.resolve_incumbent_display_name(bot_state, "hash-weird")
        assert result == "hash-weird"

    def test_falls_back_to_the_raw_hash_when_name_key_missing(self, fbld):
        bot_state = {"hash-noname": {"other_field": 1}}
        result = fbld.resolve_incumbent_display_name(bot_state, "hash-noname")
        assert result == "hash-noname"

    def test_empty_symphony_id_resolves_to_empty_string_not_a_crash(self, fbld):
        """F2's root fixture condition: a weekly-scheduler retrofit row's
        symphony_id=''. The resolver itself must not raise or fabricate a
        non-empty value — it honestly returns '' (empty), which
        build_proposal_symphony_name/_description are separately responsible
        for rendering without a blank slot (see the dedicated F2 tests on
        those two builders)."""
        result = fbld.resolve_incumbent_display_name({}, "")
        assert result == ""


# ---------------------------------------------------------------------------
# Group C: summarize_overlay (AC-3 helper)
# ---------------------------------------------------------------------------


def _dsl_asset(ticker: str) -> dict:
    return {"kind": "asset", "ticker": ticker}


def _dsl_weight_equal(children: list[dict]) -> dict:
    return {"kind": "weight", "scheme": "equal", "children": children}


def _dsl_flat_if_overlay(
    signal_ticker: str, threshold: float, vix_ticker: str, core_placeholder: str
) -> dict:
    """Local copy of test_frontrunner_builder.py's own DSL fixture builder
    (codebase convention — small fixture builders are duplicated per test
    file rather than cross-imported, see test_frontrunner_builder_template.py's
    own header note)."""
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


def _dsl_compound_overlay() -> dict:
    """Verbatim shape from frontrunner_builder.py's own _EXAMPLE_COMPOUND_OVERLAY
    (condition-type='compound', two binary sub-conditions joined by 'all')."""
    return {
        "kind": "if_compound",
        "condition": {
            "type": "compound",
            "operator": "all",
            "conditions": [
                {
                    "type": "binary",
                    "lhs_fn": "relative-strength-index",
                    "lhs_ticker": "QQQ",
                    "window": 10,
                    "comparator": "gt",
                    "rhs": {"fixed": 80},
                },
                {
                    "type": "binary",
                    "lhs_fn": "cumulative-return",
                    "lhs_ticker": "QQQ",
                    "window": 5,
                    "comparator": "lt",
                    "rhs": {"fixed": 0},
                },
            ],
        },
        "then": [_dsl_weight_equal([_dsl_asset("UVXY")])],
        "else": [_dsl_weight_equal([_dsl_asset("CORE_STRATEGY_PLACEHOLDER")])],
    }


class TestSummarizeOverlay:
    def test_flat_condition_names_signal_and_fire_ticker(self, fbld):
        overlay = _dsl_flat_if_overlay("SPY", 80, "UVXY", "CANDIDATE_PLACEHOLDER_ASSET")
        summary = fbld.summarize_overlay(overlay)
        assert "SPY" in summary, f"flat-condition summary must name the signal ticker: {summary!r}"
        assert "UVXY" in summary, f"flat-condition summary must name the fire ticker: {summary!r}"
        assert summary != "compound condition overlay", (
            "a genuinely flat/derivable condition must not fall through to the generic "
            f"fallback — got {summary!r}"
        )

    def test_two_tier_scale_in_overlay_yields_specific_summary_not_fallback(self, fbld):
        """F1 (Revise 2): _find_first_asset_ticker only ever walked a bare
        node list for a direct {"kind":"asset",...} leaf (possibly wrapped in
        a weight/group node) — it never descended into a NESTED if-node's own
        "then"/"else" lists. The 2-tier scale-in structure is the generation
        prompt's OWN flagship worked example (a lower-threshold if-node whose
        "then" branch is ANOTHER nested if-node, per HARD REQUIREMENT #4:
        "preserve it as TIERED nested if-nodes, nested inside the OUTER
        node's 'then' branch") — so this shape reaching the fallback string
        is a real, common-case regression, not an edge case. The OUTER
        condition here is genuinely FLAT (has lhs_fn) — degrading to
        "compound condition overlay" doesn't just lose detail, it MISLABELS
        a flat condition as compound."""
        inner_if = {
            "kind": "if",
            "condition": {
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": "QQQ",
                "window": 10,
                "comparator": "gt",
                "rhs": {"fixed": 82.5},
            },
            "then": [_dsl_weight_equal([_dsl_asset("UVXY")])],
            "else": [_dsl_weight_equal([_dsl_asset("VIXM")])],
        }
        outer = {
            "kind": "if",
            "condition": {
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": "QQQ",
                "window": 10,
                "comparator": "gt",
                "rhs": {"fixed": 80},
            },
            "then": [inner_if],
            "else": [_dsl_weight_equal([_dsl_asset("CORE_ASSET_0001")])],
        }
        summary = fbld.summarize_overlay(outer)
        assert summary != "compound condition overlay", (
            f"a genuinely FLAT outer condition (has lhs_fn) with a nested if-node in "
            f"its then-branch must never be mislabeled as 'compound condition overlay' "
            f"— got {summary!r}"
        )
        assert "QQQ" in summary, f"summary must name the outer signal ticker: {summary!r}"
        assert "UVXY" in summary, (
            f"summary must find the fire ticker by descending the nested if-node's own "
            f"then-branch (UVXY, the inner if's then-branch asset) — got {summary!r}"
        )

    def test_compound_condition_degrades_to_generic_literal(self, fbld):
        summary = fbld.summarize_overlay(_dsl_compound_overlay())
        assert summary == "compound condition overlay", (
            f"a compound (condition-type='compound') overlay must degrade to the exact "
            f"literal fallback — got {summary!r}"
        )

    @pytest.mark.parametrize(
        "malformed",
        [
            None,
            {},
            "not a dict",
            {"kind": "if"},
            {"kind": "if", "condition": {}},
            {"kind": "if", "condition": {"comparator": "gt"}},
        ],
    )
    def test_malformed_input_never_raises_and_returns_nonempty_string(self, fbld, malformed):
        summary = fbld.summarize_overlay(malformed)  # must not raise
        assert isinstance(summary, str) and summary, (
            f"summarize_overlay must never raise and always return a non-empty string "
            f"for malformed input {malformed!r} — got {summary!r}"
        )

    def test_missing_window_never_produces_a_none_substring(self, fbld):
        """F6 (Revise 2): condition.get("window") had no default and was
        interpolated raw into the f-string — a missing "window" key produced
        a literal "None" in the summary (e.g. "...(SPY,None) gt 80..."),
        which is then persisted, rendered on the dashboard, AND sent to
        Composer as part of the description. window must be REQUIRED for the
        specific summary; its absence degrades to the fallback."""
        overlay = {
            "kind": "if",
            "condition": {
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": "SPY",
                # "window" key deliberately omitted
                "comparator": "gt",
                "rhs": {"fixed": 80},
            },
            "then": [_dsl_weight_equal([_dsl_asset("UVXY")])],
            "else": [_dsl_weight_equal([_dsl_asset("CORE_ASSET_0001")])],
        }
        summary = fbld.summarize_overlay(overlay)
        assert "None" not in summary, (
            f"a missing 'window' field must never surface as a literal 'None' "
            f"substring — got {summary!r}"
        )

    def test_missing_rhs_fixed_never_produces_a_none_substring(self, fbld):
        """F6 (Revise 2): the same class of defect via rhs.get("fixed") on an
        rhs dict lacking the "fixed" key (or rhs itself being None/malformed)
        — rhs_val must be REQUIRED (non-None) for the specific summary; its
        absence degrades to the fallback."""
        overlay = {
            "kind": "if",
            "condition": {
                "lhs_fn": "relative-strength-index",
                "lhs_ticker": "SPY",
                "window": 10,
                "comparator": "gt",
                "rhs": {},  # "fixed" key deliberately omitted
            },
            "then": [_dsl_weight_equal([_dsl_asset("UVXY")])],
            "else": [_dsl_weight_equal([_dsl_asset("CORE_ASSET_0001")])],
        }
        summary = fbld.summarize_overlay(overlay)
        assert "None" not in summary, (
            f"a missing rhs['fixed'] field must never surface as a literal 'None' "
            f"substring — got {summary!r}"
        )


# ---------------------------------------------------------------------------
# Group D: persist-site integration — real _run_build_for_symphony run.
# Mirrors test_frontrunner_builder_signal_wiring.py's established idiom
# exactly (same incumbent_symphony construction, same shaped-result gate-
# clearing fixtures) so this test exercises the REAL accept path, not a
# re-derivation of gate/Calmar math.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_live_atlas_calls():
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value={"available": False, "candidates": [], "stats": {}, "source": "captplanet"},
    ):
        yield


@pytest.fixture
def incumbent_symphony() -> dict:
    """Real, detectable incumbent — RSI(SPY,10) gt 31 -> VIXY, else
    CORE_ASSET_0001 (matches test_frontrunner_builder_signal_wiring.py's own
    fixture exactly, threshold=31 so fr_key='SPY:10:31')."""
    from advisors import symphony_schema

    incumbent_cascade = symphony_schema.make_if(
        symphony_schema.make_condition(
            symphony_schema.make_indicator("relative-strength-index", "SPY", window=10),
            "gt",
            31,
        ),
        then_children=[symphony_schema.make_weight_equal([symphony_schema.make_asset("VIXY")])],
        else_children=[
            symphony_schema.make_weight_equal([symphony_schema.make_asset("CORE_ASSET_0001")])
        ],
    )
    return symphony_schema.make_root("Wiring Test Symphony", "daily", [incumbent_cascade])


def _make_shaped_result(shape_pct: list, n_days: int = 100):
    """Verbatim from test_frontrunner_gate_wiring.py / test_frontrunner_builder_signal_wiring.py
    — a hand-shaped, genuinely two-sided return series proven to clear the
    BHY/FDR + Calmar acceptance gate."""
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = shape_pct[i % len(shape_pct)] / 100.0
        d += timedelta(days=1)

    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08}, data_warnings=[], daily_returns=returns
    )


def _mocked_overlay_client(
    ticker: str = "SPY",
    window: int = 10,
    threshold: float = 31,
    vix_ticker: str = "UVXY",
    core_placeholder: str = "CANDIDATE_PLACEHOLDER_ASSET",
) -> MagicMock:
    """Local Fable-mock builder — DELIBERATELY uses a placeholder ticker
    (CANDIDATE_PLACEHOLDER_ASSET) DISTINCT from the incumbent fixture's own
    core ticker (CORE_ASSET_0001), so the pre-graft-vs-post-graft assertions
    below are unambiguous (mirrors test_frontrunner_builder.py's own
    documented rationale for keeping these two tickers distinct — reusing
    the same ticker for both makes an assertion pass 'by coincidence'
    regardless of whether the code under test is actually correct)."""
    overlay = {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": ticker,
            "window": window,
            "comparator": "gt",
            "rhs": {"fixed": threshold},
        },
        "then": [{"kind": "weight", "scheme": "equal", "children": [_dsl_asset(vix_ticker)]}],
        "else": [
            {"kind": "weight", "scheme": "equal", "children": [_dsl_asset(core_placeholder)]}
        ],
    }
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"overlay": overlay}
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _healthy_signals(fr_key: str = "SPY:10:31", cagr: float = 0.06, sharpe: float = 1.0) -> dict:
    return {
        "available": True,
        "reason": None,
        "signals": [
            {
                "fr_key": fr_key,
                "cagr": cagr,
                "sharpe": sharpe,
                "rsi_live": 55.0,
                "rsi_live_at": "2026-08-20T07:01:00Z",
                "sortino": None,
                "calmar": None,
                "max_drawdown": None,
                "fetch_ts": "2026-08-20T15:00:00Z",
            }
        ],
        "stats": {},
        "source": "captplanet",
    }


class TestPersistedOverlayIdentityFields:
    def test_metrics_json_carries_pregraft_overlay_and_replaced_node_id(
        self, fbld, incumbent_symphony
    ):
        from advisors import frontrunner_detector

        # Precompute the REAL detector-derived cascade id from the SAME fixture
        # object _run_build_for_symphony will operate on (via the mocked
        # fetch_symphony_score below) — never a hardcoded/guessed id.
        detection = frontrunner_detector.detect_frontrunner_cascades(incumbent_symphony)
        assert detection.cascades, "fixture setup: incumbent_symphony must have a detectable cascade"
        expected_replaced_node_id = detection.cascades[0].overlay_tree.get("id")
        assert expected_replaced_node_id, "fixture setup: cascade overlay_tree must carry an id"

        fake_signals = _healthy_signals(fr_key="SPY:10:31", cagr=0.06, sharpe=1.0)
        incumbent_shape_pct = [0.10, -0.05, 0.08, -0.10, 0.12, -0.03, 0.05, -0.08, 0.10, -0.02]
        candidate_shape_pct = [0.60, 0.60, 0.60, 0.60, -0.05, 0.60, 0.60, 0.60, 0.60, -0.05]
        incumbent_result = _make_shaped_result(incumbent_shape_pct, n_days=100)
        candidate_result = _make_shaped_result(candidate_shape_pct, n_days=100)

        call_count = {"n": 0}

        def _side_effect(*_a, **_k):
            call_count["n"] += 1
            return incumbent_result if call_count["n"] == 1 else candidate_result

        with (
            patch("symphony_logic.fetch_symphony_score", return_value=incumbent_symphony),
            patch("advisors.frontrunner_signals.load_frontrunner_signals", return_value=fake_signals),
            patch.object(fbld, "_build_client", return_value=_mocked_overlay_client()),
            patch("advisors.composer_backtest_client.run_backtest", side_effect=_side_effect),
            patch("database.insert_frontrunner_proposal") as mock_insert,
            patch("database.insert_dof_ledger_row"),
        ):
            fbld._run_build_for_symphony("wiring-test-symphony")

        assert mock_insert.called, (
            "expected the surviving candidate to be queued — this test reuses the exact "
            "fixture shapes proven to clear the gate/Calmar accept path elsewhere in the "
            "suite; if this fails, the accept path itself regressed, not overlay-identity "
            "wiring specifically"
        )
        _, call_kwargs = mock_insert.call_args
        metrics = call_kwargs.get("metrics_json")
        assert isinstance(metrics, dict)

        for key in ("overlay_tree", "replaced_node_id", "overlay_summary"):
            assert key in metrics, (
                f"metrics_json is missing {key!r} — AC-3 requires the persisted proposal "
                f"to carry it. got keys={sorted(metrics.keys())}"
            )

        # replaced_node_id must be the REAL detector-derived cascade id.
        assert metrics["replaced_node_id"] == expected_replaced_node_id

        overlay_tree = metrics["overlay_tree"]
        assert isinstance(overlay_tree, dict)
        # PRE-GRAFT/PRE-COMPILE proof: the raw DSL shape has "kind", never "step"
        # (a compiled or post-graft node would carry "step").
        assert overlay_tree.get("kind") == "if", (
            f"overlay_tree must be the raw pre-compile DSL candidate node (kind='if'), "
            f"not a compiled/spliced Composer node — got keys={sorted(overlay_tree.keys())}"
        )
        assert "step" not in overlay_tree, (
            "overlay_tree carries a 'step' key — this is the COMPILED/post-graft shape, "
            "not the raw DSL candidate. MOD 1 requires persisting result.candidate "
            "(pre-compile, pre-graft), never the post-splice node."
        )

        # ADVERSARIAL GUARD (MOD 1(b)): the candidate's own placeholder ticker must
        # still be present in overlay_tree's else branch — proving this is the
        # PRE-GRAFT node. If someone "fixes" this to persist the post-graft node
        # instead, the placeholder will have been REPLACED by the incumbent's real
        # core content and this ticker will be gone.
        overlay_json = __import__("json").dumps(overlay_tree)
        assert "CANDIDATE_PLACEHOLDER_ASSET" in overlay_json, (
            "overlay_tree's placeholder ticker is missing — this strongly suggests the "
            "POST-GRAFT node was persisted instead of the pre-graft candidate (MOD 1 "
            "regression: the incumbent's real core content silently replaced the "
            "placeholder)."
        )
        assert "CORE_ASSET_0001" not in overlay_json, (
            "overlay_tree contains the incumbent's real core ticker (CORE_ASSET_0001) — "
            "this is only possible if the POST-GRAFT node (which contains the ENTIRE "
            "incumbent core) was persisted instead of the small pre-graft candidate "
            "(MOD 1 regression)."
        )

        # ADVERSARIAL GUARD (MOD 1(b)): a positive/negative pair proving the graft
        # genuinely happened SOMEWHERE in the full candidate_tree (positive: the
        # incumbent's real core ticker survives in the full tree — the graft did
        # its job) while overlay_tree itself remains the small pre-graft node
        # (negative, already asserted above: CORE_ASSET_0001 absent from
        # overlay_tree). Note: the compiled node's id is freshly generated during
        # compile_plan (never equal to replaced_node_id, which identifies the
        # REMOVED incumbent node — that id no longer exists anywhere in the
        # resulting tree), so locating the exact post-graft subtree by id is not
        # possible from outside the module; this positive/negative ticker pair is
        # the robust, implementation-detail-free proof instead.
        assert "candidate_tree" in call_kwargs
        candidate_tree_json = __import__("json").dumps(call_kwargs["candidate_tree"])
        assert "CORE_ASSET_0001" in candidate_tree_json, (
            "the incumbent's real core ticker (CORE_ASSET_0001) must survive "
            "SOMEWHERE in the full spliced candidate_tree — proving the graft "
            "actually preserved the incumbent's core content."
        )

        assert isinstance(metrics["overlay_summary"], str) and metrics["overlay_summary"], (
            "overlay_summary must be a non-empty string"
        )

        # Existing metrics keys must be byte-unchanged (KEY-SET check only — never
        # asserting exact producer-computed float values here, those are covered
        # by test_frontrunner_builder_signal_wiring.py itself).
        for existing_key in (
            "candidate_cagr",
            "candidate_calmar",
            "candidate_max_drawdown",
            "incumbent_cagr",
            "incumbent_calmar",
            "incumbent_max_drawdown",
            "signal_provenance",
        ):
            assert existing_key in metrics, (
                f"pre-existing metrics_json key {existing_key!r} went missing — AC-3 keys "
                f"must be strictly additive"
            )


# ---------------------------------------------------------------------------
# Group E: approve_frontrunner_proposal wiring (AC-1, AC-7-approve, AC-6-approve).
# Patch-target convention mirrors test_frontrunner_approval.py exactly:
# patch collaborators at their ORIGIN module (database.*, advisors.
# composer_draft_client.*) — never approve_frontrunner_proposal's own module
# attributes.
# ---------------------------------------------------------------------------


def _make_proposal(
    *,
    proposal_id: int = 1,
    symphony_id: str = "test-symphony-hash",
    proposal_source: str = "frontrunner_builder",
    approval_status: str = "pending",
    created_symphony_id: str | None = None,
    created_at: str = "2026-08-20T00:00:00Z",
    metrics_json: dict | None = None,
) -> dict:
    return {
        "id": proposal_id,
        "created_at": created_at,
        "updated_at": created_at,
        "symphony_id": symphony_id,
        "proposal_source": proposal_source,
        "approval_status": approval_status,
        "candidate_tree": {"step": "root", "children": []},
        "metrics_json": metrics_json
        if metrics_json is not None
        else {
            "candidate_cagr": 0.1,
            "replaced_node_id": "node-abc",
            "overlay_summary": "RSI(SPY,10) > 80 hedges into UVXY",
        },
        "created_symphony_id": created_symphony_id,
        "error_message": None,
    }


def _draft_success(symphony_id: str = "new-symphony-123", version_id: str = "v1"):
    from advisors.composer_draft_client import DraftResult

    return DraftResult(success=True, symphony_id=symphony_id, version_id=version_id, error=None)


class TestApproveNameAndDescriptionWiring:
    def test_resolvable_display_name_produces_ac1_locked_format(self, fbld):
        proposal = _make_proposal(proposal_id=5, symphony_id="hash-abc123")
        bot_state = {"hash-abc123": {"name": "My Real Symphony"}}
        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value=bot_state),
            patch(
                "advisors.composer_draft_client.save_symphony", return_value=_draft_success()
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            result = fbld.approve_frontrunner_proposal(5)

        assert result.success is True
        _, kwargs = mock_save.call_args
        assert kwargs["name"] == "My Real Symphony + FR overlay (full copy, #5)", (
            f"got name={kwargs.get('name')!r}"
        )

    def test_unresolvable_display_name_falls_back_to_the_raw_hash(self, fbld):
        proposal = _make_proposal(proposal_id=7, symphony_id="unresolvable-hash-999")
        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={}),
            patch(
                "advisors.composer_draft_client.save_symphony", return_value=_draft_success()
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            fbld.approve_frontrunner_proposal(7)

        _, kwargs = mock_save.call_args
        assert kwargs["name"] == "unresolvable-hash-999 + FR overlay (full copy, #7)", (
            f"got name={kwargs.get('name')!r}"
        )

    def test_bare_hash_legacy_format_never_appears_in_any_approve_call(self, fbld):
        """Adversarial (team-lead-requested): the OLD 'Frontrunner Candidate — <hash>'
        literal must never appear in any save_symphony call, resolvable or not."""
        proposal = _make_proposal(proposal_id=1, symphony_id="some-hash")
        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={}),
            patch(
                "advisors.composer_draft_client.save_symphony", return_value=_draft_success()
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            fbld.approve_frontrunner_proposal(1)

        _, kwargs = mock_save.call_args
        assert "Frontrunner Candidate — " not in kwargs.get("name", ""), (
            f"the legacy bare-hash name format leaked through — got "
            f"name={kwargs.get('name')!r}"
        )

    def test_retrofit_source_uses_strategy_builder_candidate_wording_not_fr_overlay(self, fbld):
        proposal = _make_proposal(
            proposal_id=9,
            symphony_id="hash-xyz",
            proposal_source="strategy_builder_retrofit",
            metrics_json={"cagr": 0.1},
        )
        bot_state = {"hash-xyz": {"name": "My Real Symphony"}}
        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value=bot_state),
            patch(
                "advisors.composer_draft_client.save_symphony", return_value=_draft_success()
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            fbld.approve_frontrunner_proposal(9)

        _, kwargs = mock_save.call_args
        assert kwargs["name"] == "Strategy Builder candidate for My Real Symphony (from scratch, #9)"
        assert "FR overlay" not in kwargs["name"]

    def test_two_proposals_same_run_get_unique_names_on_approve(self, fbld):
        bot_state = {"hash-a": {"name": "My Symphony"}}
        names = []
        for pid in (1, 2):
            proposal = _make_proposal(proposal_id=pid, symphony_id="hash-a")
            with (
                patch("database.get_frontrunner_proposal", return_value=proposal),
                patch("database.load_state", return_value=bot_state),
                patch(
                    "advisors.composer_draft_client.save_symphony",
                    return_value=_draft_success(symphony_id=f"new-{pid}"),
                ) as mock_save,
                patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
                patch("database.update_frontrunner_proposal_status"),
                patch("database.insert_advisor_observation"),
            ):
                fbld.approve_frontrunner_proposal(pid)
            names.append(mock_save.call_args.kwargs["name"])

        assert names[0] != names[1], f"expected unique names, got {names!r}"

    def test_legacy_row_without_overlay_metrics_still_approves_with_degraded_description(
        self, fbld
    ):
        """AC-6-approve: a legacy proposal row (no overlay_tree/replaced_node_id/
        overlay_summary in metrics_json) must not crash the approve path — the
        description degrades gracefully via build_proposal_symphony_description's
        own AC-6 handling."""
        proposal = _make_proposal(
            proposal_id=3, symphony_id="hash-legacy", metrics_json={"candidate_cagr": 0.05}
        )
        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={"hash-legacy": {"name": "Legacy Sym"}}),
            patch(
                "advisors.composer_draft_client.save_symphony", return_value=_draft_success()
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            result = fbld.approve_frontrunner_proposal(3)

        assert result.success is True
        _, kwargs = mock_save.call_args
        description = kwargs.get("description", "")
        assert "None" not in description, (
            f"legacy-row description leaked a bare 'None' — got description={description!r}"
        )
        # Must be the NEW build_proposal_symphony_description output, not the old
        # generic hardcoded string — otherwise this test is vacuous (the old code
        # never touches metrics_json at all, so it can't crash regardless of what's
        # missing, and trivially passes without proving anything new happened).
        assert description != "Frontrunner Builder candidate (operator-approved)", (
            "the legacy-row approve path is still sending the OLD generic hardcoded "
            "description — it must route through build_proposal_symphony_description "
            "even when AC-3 overlay keys are absent from metrics_json"
        )
        assert "Legacy Sym" in description, (
            f"description must still carry the resolved display name — got "
            f"description={description!r}"
        )
        assert "Undeployed candidate — review before investing." in description

    def test_truthy_non_dict_metrics_json_approves_cleanly_no_crash(self, fbld):
        """F3 (Revise 2): the approve path guarded metrics_json with
        `proposal.get("metrics_json") or {}` — a TRUTHY non-dict (a stored
        JSON array, e.g. [1, 2, 3]) is not falsy, so `or {}` never kicks in,
        and the subsequent `.get("replaced_node_id")` call raises
        AttributeError. The function's own outer except catches it (so it
        doesn't crash the CALLER), but save_symphony is never reached and no
        error_message is persisted to the DB — unlike every OTHER failure
        branch in this same function, which explicitly persists a reason
        before returning failure. Fix: an isinstance-dict guard (mirroring
        app.py's own AC-6 guard) so this path never raises at all — approval
        proceeds normally with a degraded (no-overlay-detail) description."""
        proposal = _make_proposal(
            proposal_id=8, symphony_id="hash-listmetrics", metrics_json=[1, 2, 3]
        )
        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={"hash-listmetrics": {"name": "Sym"}}),
            patch(
                "advisors.composer_draft_client.save_symphony", return_value=_draft_success()
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            result = fbld.approve_frontrunner_proposal(8)

        assert mock_save.called, (
            "save_symphony was never called — the approve path crashed on the "
            "truthy-non-dict metrics_json before reaching the Composer create call"
        )
        assert result.success is True, (
            f"a truthy-non-dict metrics_json must not prevent a successful approve — "
            f"got success={result.success!r} error={result.error!r}"
        )

    def test_empty_symphony_id_name_omits_blank_identity_slot_on_approve(self, fbld):
        """F2 (Revise 2), approve-path integration: a weekly-scheduler
        retrofit proposal's symphony_id='' (real production condition —
        those rows are keyed to symphony_id="" per
        advisors/strategy_builder_scheduler.py's all-observations-keyed-to-
        symphony_id="" convention) must not produce a blank-slot Composer
        name like 'Strategy Builder candidate for  (from scratch, #N)'."""
        proposal = _make_proposal(
            proposal_id=11, symphony_id="", proposal_source="strategy_builder_retrofit"
        )
        with (
            patch("database.get_frontrunner_proposal", return_value=proposal),
            patch("database.load_state", return_value={}),
            patch(
                "advisors.composer_draft_client.save_symphony", return_value=_draft_success()
            ) as mock_save,
            patch("advisors.composer_draft_client.verify_undeployed", return_value=True),
            patch("database.update_frontrunner_proposal_status"),
            patch("database.insert_advisor_observation"),
        ):
            fbld.approve_frontrunner_proposal(11)

        _, kwargs = mock_save.call_args
        name = kwargs.get("name", "")
        assert "  " not in name, f"a blank identity slot (double space) leaked: {name!r}"
        assert name == "Strategy Builder candidate (from scratch, #11)", f"got {name!r}"
